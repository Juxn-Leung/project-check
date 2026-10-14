"""Source-bound acceptance seals and a conservative local OpenSpec archive bridge.

Hash seals detect stale/changed evidence, not malicious repository writers.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

import project_check as pc
import workspace_state


def now():
    return datetime.now(timezone.utc).isoformat()


def immutable(path, value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        pc.require(path.read_text(encoding="utf-8") == text, f"Refusing to overwrite frozen artifact: {path.name}")
    else:
        with path.open("x", encoding="utf-8") as file:
            file.write(text)


def locations(root, run_dir):
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    runs = (root / ".project-check/runs").resolve()
    pc.require(run_dir.is_dir() and run_dir.is_relative_to(runs) and run_dir != runs and runs.is_relative_to(root),
               "Completion requires an existing run under .project-check/runs inside the project")
    return root, run_dir


@contextmanager
def lock(path):
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(now())
    except FileExistsError as error:
        raise ValueError(f"Completion locked: {path}; inspect interrupted work before removing lock") from error
    try:
        yield
    finally:
        path.unlink()


def file_row(base, name):
    pc.require(pc.evidence_exists(name, base), f"Missing/unsafe completion artifact: {name}")
    return {"path": name, "sha256": pc.sha256(base / name)}


def check_seal(run_dir):
    data = pc.read_json(run_dir / "seal.json")
    pc.require(data.get("version") == 1 and data.get("outcome") == "PASSED", "Invalid acceptance seal")
    rows = data.get("artifacts", [])
    pc.require(isinstance(rows, list) and rows, "Seal has no artifacts")
    names = set()
    for row in rows:
        name = row.get("path")
        pc.require(name not in names and file_row(run_dir, name)["sha256"] == row.get("sha256"),
                   f"Sealed evidence changed: {name}")
        names.add(name)
    pc.require({"results.json", "workspace.json", "inventory.json", "project.json", "sealed-report.json", "sealed-report.md"} <= names,
               "Seal omits required artifacts")
    return data


def checked_run(root, run_dir, module=None):
    inventory, payload = pc.read_json(run_dir / "inventory.json"), pc.read_json(run_dir / "results.json")
    pc.require(inventory.get("version") == payload.get("version") == 2, "Completion requires v2 inventory/results")
    scope = payload["run"].get("scope", "")
    pc.require(scope == "all" or scope.startswith("module:"), "Unsupported run scope")
    actual_module = None if scope == "all" else scope.split(":", 1)[1]
    pc.require(module is None or pc.scope_modules(inventory, module)[0] == scope, "Seal scope mismatch")
    summary = pc.summarize(inventory, payload, run_dir, actual_module)
    pc.require(summary["outcome"] == "PASSED", "Acceptance is not PASSED: " + "; ".join(summary["gaps"]))
    manifests = [payload["run"]["snapshot_manifest"]] + payload["run"]["evidence_manifests"]
    names = {"results.json"}
    entries, problems = pc.verified_manifest(manifests[0], run_dir)
    pc.require(not problems and pc.artifact_ok("workspace.json", run_dir, entries), "Frozen workspace.json missing from valid snapshot manifest")
    for manifest in manifests:
        entries, problems = pc.verified_manifest(manifest, run_dir)
        pc.require(not problems, "Evidence manifest changed: " + "; ".join(problems))
        names.add(manifest)
        names.update(entries)
    snapshot = pc.read_json(run_dir / "workspace.json")
    fresh = workspace_state.current(snapshot, root)
    pc.require(fresh["current"], "Source changed since acceptance snapshot: " + ", ".join(x["path"] for x in fresh["changes"][:12]))
    return inventory, payload, summary, snapshot, names


def seal(root, run_dir, module=None):
    root, run_dir = locations(root, run_dir)
    with lock(run_dir / ".completion.lock"):
        _, payload, summary, snapshot, names = checked_run(root, run_dir, module)
        if (run_dir / "seal.json").exists():
            return check_seal(run_dir)
        immutable(run_dir / "sealed-report.json", summary)
        immutable(run_dir / "sealed-report.md", pc.render_report(summary))
        names.update(("sealed-report.json", "sealed-report.md"))
        rows = [file_row(run_dir, name) for name in sorted(names)]
        pc.require(workspace_state.current(snapshot, root)["current"], "Source changed while sealing")
        data = {"version": 1, "outcome": "PASSED", "sealed_at": now(), "run_id": payload["run"]["id"],
                "scope": payload["run"]["scope"], "selected_ids": payload["run"]["selected_ids"],
                "source_fingerprint": snapshot["fingerprint"], "artifacts": rows}
        immutable(run_dir / "seal.json", data)
        return data


def change_path(root, change):
    pc.require(isinstance(change, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", change), "Invalid OpenSpec change ID")
    path = root / "openspec/changes" / change
    pc.require(path.resolve().is_relative_to(root), "OpenSpec change escapes project root")
    return path


def markdown_lines(text):
    """Ignore fenced examples and comments when finding requirements or tasks."""
    fence, comment = None, False
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if fence is not None:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1] and not marker.group(2).strip():
                fence = None
            continue
        if not comment and marker and not (marker.group(1)[0] == '`' and '`' in marker.group(2)):
            fence = (marker.group(1)[0], len(marker.group(1)))
            continue
        visible = ''
        while line:
            delimiter = '-->' if comment else '<!--'
            index = line.find(delimiter)
            if index < 0:
                if not comment:
                    visible += line
                break
            if not comment:
                visible += line[:index]
            line = line[index + len(delimiter):]
            comment = not comment
        line = visible
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if marker and not (marker.group(1)[0] == '`' and '`' in marker.group(2)):
            fence = (marker.group(1)[0], len(marker.group(1)))
            continue
        yield line


def parse_specs(root, change):
    """Supported delta headings only. Unknown formats produce explicit gaps."""
    root = Path(root).resolve()
    directory = change_path(root, change)
    pc.require(directory.is_dir(), "OpenSpec change does not exist")
    requirements, problems = [], []
    supported = set(directory.glob("specs/*/spec.md"))
    unsupported = set(directory.glob("specs/**/*.md")) - supported
    problems.extend("Unsupported spec path: " + str(path.relative_to(root)) for path in sorted(unsupported))
    for path in sorted(supported):
        pc.require(path.resolve().is_relative_to(root), "OpenSpec spec escapes project root")
        current, section = None, None
        relative = str(path.relative_to(root))
        for line in markdown_lines(path.read_text(encoding="utf-8")):
            section_match = re.match(r"^##\s+(.+?)\s*$", line)
            if section_match:
                section, current = section_match.group(1), None
                if section not in {"ADDED Requirements", "MODIFIED Requirements", "REMOVED Requirements"}:
                    problems.append(f"Unsupported delta section {relative}: {section}")
                continue
            req = re.match(r"^### Requirement:\s*(.+?)\s*$", line)
            scenario = re.match(r"^#### Scenario:\s*(.+?)\s*$", line)
            if req:
                if section not in {"ADDED Requirements", "MODIFIED Requirements", "REMOVED Requirements"}:
                    problems.append("Requirement outside supported delta section: " + req.group(1))
                current = {"spec": path.parent.name, "requirement": req.group(1), "section": section,
                           "path": relative, "sha256": pc.sha256(path), "scenarios": []}
                requirements.append(current)
            elif scenario:
                if current is None:
                    problems.append("Scenario without requirement: " + scenario.group(1))
                else:
                    current["scenarios"].append(scenario.group(1))
            elif re.match(r"^#{3,}\s+", line) and re.search(r"\b(Requirement|Scenario)\b", line):
                problems.append("Unsupported requirement/scenario heading: " + line)
        if not any(x["path"] == relative for x in requirements):
            problems.append("No supported requirements in " + relative)
    keys = [(r["spec"], r["requirement"]) for r in requirements]
    if len(keys) != len(set(keys)):
        problems.append("Duplicate OpenSpec requirement headings")
    for req in requirements:
        if len(req["scenarios"]) != len(set(req["scenarios"])):
            problems.append("Duplicate OpenSpec scenario headings")
        if req["section"] != "REMOVED Requirements" and not req["scenarios"]:
            problems.append("Requirement has no scenarios: " + req["requirement"])
    return {"change": change, "requirements": requirements, "problems": problems}


def policy_for(project, change):
    policy = project.get("completion", {})
    pc.require(isinstance(policy, dict), "completion policy must be an object")
    argv = policy.get("openspec_argv", ["openspec"])
    pc.strings(argv, "completion.openspec_argv", False)
    pc.require(not any(x.startswith(("--no-validate", "--skip-specs", "--store")) for x in argv), "Unsupported OpenSpec command prefix")
    timeout = policy.get("timeout_seconds", 30)
    pc.require(type(timeout) in (int, float) and 0 < timeout <= 300, "Invalid OpenSpec timeout")
    pc.require(type(policy.get("auto_archive", False)) is bool, "auto_archive must be boolean")
    mappings = policy.get("changes", {})
    pc.require(isinstance(mappings, dict) and isinstance(mappings.get(change), dict), "No explicit OpenSpec change mapping")
    mapping = mappings[change]
    for key in ("requirement_ids", "scenario_ids"):
        ids = pc.strings(mapping.get(key), "completion." + key, False)
        pc.require(len(ids) == len(set(ids)), "Duplicate completion mapping")
    pc.require(type(mapping.get("human_approval_required", False)) is bool, "human_approval_required must be boolean")
    return policy, mapping, argv, timeout


def mapping_gaps(root, change, inventory, mapping, selected_ids):
    parsed = parse_specs(root, change)
    gaps = parsed["problems"][:]
    requirements = {r["id"]: r for r in inventory["requirements"] if r["state"] == "active"}
    scenarios = {s["id"]: s for s in inventory["scenarios"] if s["state"] == "active"}
    sources = {s["id"]: s for s in inventory["requirement_sources"]}
    req_ids, scenario_ids = set(mapping["requirement_ids"]), set(mapping["scenario_ids"])
    if not req_ids <= requirements.keys() or not scenario_ids <= scenarios.keys():
        return ["Change maps unknown/retired requirement or scenario IDs"], parsed
    for item in list(requirements.values()) + list(scenarios.values()):
        pc.require(isinstance(item.get("openspec", {}), dict), "OpenSpec inventory mapping must be an object: " + item["id"])
    declared = {r["id"] for r in requirements.values() if r.get("openspec", {}).get("change") == change}
    if declared != req_ids:
        gaps.append("Explicit change requirement IDs differ from inventory OpenSpec mappings")
    parsed_keys = {(r["spec"], r["requirement"]) for r in parsed["requirements"]}
    mapped_keys = {(requirements[id].get("openspec", {}).get("spec"), requirements[id].get("openspec", {}).get("requirement")) for id in req_ids}
    if parsed_keys != mapped_keys:
        gaps.append("Delta requirement headings are not completely and exactly mapped")
    if not parsed["requirements"]:
        gaps.append("No supported spec deltas; refactor/custom-schema changes require manual workflow")
    for req in parsed["requirements"]:
        matches = [r for r in requirements.values() if r["id"] in req_ids and r.get("openspec", {}).get("spec") == req["spec"]
                   and r.get("openspec", {}).get("requirement") == req["requirement"]]
        if len(matches) != 1:
            gaps.append("Requirement mapping must be unique: " + req["requirement"])
            continue
        item = matches[0]
        source = sources[item["source"]]
        if source.get("path") != req["path"] or source.get("sha256") != req["sha256"]:
            gaps.append("Requirement source is not this exact delta spec: " + item["id"])
        for title in req["scenarios"]:
            matches = [s for s in scenarios.values() if s["id"] in scenario_ids and item["id"] in s["requirement_ids"]
                       and s.get("openspec") == {"change": change, "spec": req["spec"], "requirement": req["requirement"], "scenario": title}]
            if not matches:
                gaps.append("Unmapped OpenSpec scenario: " + req["requirement"] + " / " + title)
    required_modules = set()
    for id in req_ids:
        required_modules.update(pc.scope_modules(inventory, requirements[id]["module"])[1])
    required_scenarios = {s["id"] for s in scenarios.values() if s["module"] in required_modules}
    if not scenario_ids <= required_scenarios:
        gaps.append("Explicit scenario mapping is outside change/regression modules")
    if not required_scenarios <= set(selected_ids):
        gaps.append("Run omits change or regression scenarios: " + ", ".join(sorted(required_scenarios - set(selected_ids))))
    if any(not req_ids.intersection(scenarios[id]["requirement_ids"]) for id in scenario_ids):
        gaps.append("Explicit scenario IDs must map change requirements; regression is derived separately")
    return gaps, parsed


def invoke(root, argv, suffix, timeout, structured=True):
    try:
        result = subprocess.run(argv + suffix, cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "argv": argv + suffix}
    except OSError as error:
        return {"ok": False, "error": str(error), "argv": argv + suffix}
    output = {"ok": result.returncode == 0, "returncode": result.returncode, "argv": argv + suffix,
              "stdout": result.stdout, "stderr": result.stderr}
    if structured and output["ok"]:
        try:
            output["data"] = json.loads(result.stdout)
            pc.require(isinstance(output["data"], dict), "OpenSpec JSON must be an object")
        except (ValueError, TypeError) as error:
            output.update(ok=False, error="Invalid OpenSpec JSON: " + str(error))
    return output


def directory_hashes(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        pc.require(not path.is_symlink(), "Symlinks are unsupported in archived change evidence")
        if path.is_file():
            result[str(path.relative_to(directory))] = pc.sha256(path)
    return result


def archive_match(root, change, before):
    candidates = []
    for path in (root / "openspec/changes/archive").glob("????-??-??-" + change):
        if path.is_dir() and path.resolve().is_relative_to(root) and directory_hashes(path) == before:
            candidates.append(path)
    return candidates[0] if not change_path(root, change).exists() and len(candidates) == 1 else None


def source_bindings(root, inventory, change, archive):
    prefix = str(Path("openspec/changes") / change) + "/"
    bindings = []
    for source in inventory["requirement_sources"]:
        if source["kind"] != "file":
            continue
        path = root / source["path"]
        if path.is_file() and pc.sha256(path) == source["sha256"]:
            continue
        target = archive / source["path"][len(prefix):] if source["path"].startswith(prefix) else None
        exact = bool(target and target.is_file() and pc.sha256(target) == source["sha256"])
        bindings.append({"source_id": source["id"], "previous_path": source["path"], "sha256": source["sha256"],
                         "state": "relocated" if exact else "pending_rebind", "path": str(target.relative_to(root)) if exact else None,
                         "reason": "Identical archived source; historical identity preserved" if exact else
                                   "Source changed during synchronization; review semantics and rebind before future acceptance"})
    return bindings


def record_archive(root, run_dir, change, intent, operation, preflight):
    archive = archive_match(root, change, intent["before"])
    if archive is None:
        return {**preflight, "outcome": "BLOCKED", "gaps": ["Archive not verified on disk; inspect command and active/archive directories before retry"],
                "archive_command": operation}
    inventory = pc.read_json(run_dir / "inventory.json")
    bindings = source_bindings(root, inventory, change, archive)
    before_source = pc.read_json(run_dir / "workspace.json")
    after_source = workspace_state.capture(root)
    unexpected = [x for x in workspace_state.changes(before_source, after_source)
                  if not (x["path"].startswith("openspec/specs/") or x["path"].startswith("openspec/changes/" + change + "/")
                          or x["path"].startswith(str(archive.relative_to(root)) + "/"))]
    data = {**preflight, "outcome": "ARCHIVED_PENDING_REBIND" if any(x["state"] == "pending_rebind" for x in bindings) else "ARCHIVED",
            "archived_at": now(), "archive_path": str(archive.relative_to(root)), "archived_files": intent["before"],
            "archive_command": operation, "source_bindings": bindings, "post_archive_source": after_source["fingerprint"]}
    if before_source.get("revision") != after_source.get("revision"):
        unexpected.append({"path": "<git revision>", "change": "modified"})
    if unexpected:
        data.update(outcome="BLOCKED", gaps=["Archive exists, but unrelated source changed during archive; rerun acceptance"], unexpected_source_changes=unexpected)
    immutable(run_dir / ("archive-" + change + ".json"), data)
    immutable(run_dir / ("archive-" + change + ".md"), "# OpenSpec completion\n\n" + data["outcome"] + "\n\nChange: `" + change +
              "`\n\nHistorical evidence stays frozen. Learning review does not block technical completion.\n")
    return data


def finish(root, run_dir, change, execute=False):
    root, run_dir = locations(root, run_dir)
    change_path(root, change)
    with lock(root / ".project-check/runs/.archive.lock"), lock(run_dir / ".completion.lock"):
        data = check_seal(run_dir)
        seal_hash = pc.sha256(run_dir / "seal.json")
        receipt_path, intent_path = run_dir / ("archive-" + change + ".json"), run_dir / ("archive-intent-" + change + ".json")
        if receipt_path.exists():
            receipt = pc.read_json(receipt_path)
            pc.require(receipt.get("seal_sha256") == seal_hash, "Archive receipt does not match seal")
            archive = root / receipt["archive_path"]
            pc.require(archive.resolve().is_relative_to(root) and archive.is_dir() and directory_hashes(archive) == receipt["archived_files"],
                       "Archived change evidence changed or missing")
            return {**receipt, "already_archived": True, "current_source": workspace_state.capture(root)["fingerprint"] == receipt["post_archive_source"]}
        if intent_path.exists():
            intent = pc.read_json(intent_path)
            pc.require(intent.get("seal_sha256") == seal_hash, "Archive intent does not match seal")
            operation_path = run_dir / ("archive-command-" + change + ".json")
            operation = pc.read_json(operation_path) if operation_path.exists() else {"ok": False, "error": "interrupted; command outcome unknown"}
            return record_archive(root, run_dir, change, intent, operation, intent["preflight"])
        inventory, _, _, snapshot, _ = checked_run(root, run_dir)
        pc.require(data["source_fingerprint"] == snapshot["fingerprint"], "Seal source identity mismatch")
        project = pc.read_json(root / ".project-check/project.json")
        pc.require(pc.read_json(root / ".project-check/inventory.json") == inventory, "Current inventory differs from sealed inventory")
        policy, mapping, argv, timeout = policy_for(project, change)
        gaps, _ = mapping_gaps(root, change, inventory, mapping, data["selected_ids"])
        active = change_path(root, change)
        before = directory_hashes(active)
        tasks = active / "tasks.md"
        task_text = '\n'.join(markdown_lines(tasks.read_text(encoding='utf-8'))) if tasks.is_file() else ''
        task_checks = re.findall(r"^\s*(?:[-*+]|\d+[.)])\s+\[([^\]])\]", task_text, re.M)
        if not task_checks or any(state.lower() != "x" for state in task_checks):
            gaps.append("Implementation tasks missing or unfinished; never auto-check task boxes")
        status = invoke(root, argv, ["status", "--change", change, "--json"], timeout)
        inputs = invoke(root, argv, ["instructions", "archive", "--change", change, "--json"], timeout)
        if not status["ok"] or not inputs["ok"]:
            gaps.append("OpenSpec status/archive inputs unavailable; inspect command details")
        elif status["data"].get("changeName") != change or status["data"].get("isPlanningComplete", status["data"].get("isComplete")) is not True:
            gaps.append("OpenSpec planning artifacts incomplete or change identity differs")
        if inputs.get("data", {}).get("changeName", change) != change:
            gaps.append("OpenSpec archive inputs identify a different change")
        decision = None
        if mapping.get("human_approval_required"):
            path = run_dir / ("human-approval-" + change + ".json")
            if path.is_file():
                decision = pc.read_json(path)
            if not (isinstance(decision, dict) and decision.get("approved") is True and decision.get("actor") == "human"
                    and decision.get("change") == change and decision.get("seal_sha256") == seal_hash and pc.nonempty(decision.get("evidence"))):
                gaps.append("Explicit human approval required: record the user's actual decision bound to this seal")
        if execute and policy.get("auto_archive") is not True:
            gaps.append("Automatic archive is not authorized by saved completion.auto_archive policy")
        result = {"version": 1, "outcome": "BLOCKED" if gaps else "READY", "change": change, "run_id": data["run_id"], "gaps": gaps,
                  "source_fingerprint": snapshot["fingerprint"], "seal_sha256": seal_hash, "openspec": {"status": status, "archive_inputs": inputs},
                  "execute": execute, "human_review": "independent of technical completion", "required_human_decision": decision}
        if gaps or not execute:
            return result
        check_seal(run_dir)
        pc.require(workspace_state.current(snapshot, root)["current"], "Source changed during completion preflight")
        intent = {"version": 1, "change": change, "seal_sha256": seal_hash, "before": before, "preflight": result}
        immutable(intent_path, intent)
        operation = invoke(root, argv, ["archive", change, "--yes"], timeout, structured=False)
        immutable(run_dir / ("archive-command-" + change + ".json"), operation)
        return record_archive(root, run_dir, change, intent, operation, result)
