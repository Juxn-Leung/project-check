"""Import pytest JUnit XML or Playwright JSON and freeze their evidence.

The adapter never runs tests. It writes one immutable import per runner/run directory.
"""
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _pytest_cases(native):
    try:
        root = ET.parse(native).getroot()
    except ET.ParseError as error:
        raise ValueError(f"invalid pytest JUnit XML: {error}") from error
    cases = []
    for node in root.iter("testcase"):
        name, classname = node.get("name"), node.get("classname")
        if not name or not classname:
            raise ValueError("pytest JUnit testcase needs classname and name")
        status = "passed"
        if node.find("failure") is not None or node.find("error") is not None:
            status = "failed"
        elif node.find("skipped") is not None:
            status = "skipped"
        flaky = any(child.tag.lower().startswith(("flaky", "rerun")) for child in node)
        cases.append({"id": f"{classname}::{name}", "status": status,
                      "flaky": flaky, "attachments": [], "results": []})
    if not cases:
        raise ValueError("pytest JUnit report contains no testcases")
    return cases


def _playwright_cases(native):
    data = json.loads(native.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("suites"), list):
        raise ValueError("invalid Playwright JSON report")
    cases = []

    def walk(suite, titles=(), file_name=None):
        if not isinstance(suite, dict):
            raise ValueError("invalid Playwright suite")
        current_file = suite.get("file") or file_name
        title = suite.get("title")
        # The outer suite often names the file itself; it is already in the ID.
        is_file_suite = not titles and current_file and title in {current_file, Path(current_file).name}
        next_titles = titles + ((title,) if title and not is_file_suite else ())
        for spec in suite.get("specs", []):
            if not isinstance(spec.get("tests"), list):
                raise ValueError("invalid Playwright spec")
            spec_file = spec.get("file") or current_file
            if not spec_file or not spec.get("title"):
                raise ValueError("Playwright spec needs file and title")
            for test in spec["tests"]:
                if not isinstance(test, dict):
                    raise ValueError("invalid Playwright test")
                project = test.get("projectName") or "default"
                case_id = "::".join((project, spec_file.replace("\\", "/"), *next_titles, spec["title"]))
                results = test.get("results", [])
                if not isinstance(results, list):
                    raise ValueError(f"{case_id}: invalid results")
                if not all(isinstance(result, dict) for result in results):
                    raise ValueError(f"{case_id}: invalid attempt")
                final = results[-1].get("status") if results else None
                status = {"passed": "passed", "failed": "failed", "timedOut": "failed",
                          "skipped": "skipped", "interrupted": "blocked"}.get(final, "blocked")
                if test.get("expectedStatus") == "failed" and test.get("status") == "expected" and final == "failed":
                    status = "passed"
                if test.get("status") == "unexpected":
                    status = "failed"
                flaky = len(results) > 1 or test.get("status") == "flaky" or any(
                    isinstance(result.get("retry"), int) and result["retry"] > 0 for result in results)
                if flaky and status == "passed":
                    status = "failed"
                cases.append({"id": case_id, "status": status, "flaky": flaky,
                              "attachments": [attachment for result in results for attachment in result.get("attachments", [])],
                              "results": results})
        for child in suite.get("suites", []):
            walk(child, next_titles, current_file)

    for suite in data["suites"]:
        walk(suite)
    if not cases:
        raise ValueError("Playwright JSON report contains no testcases")
    return cases


def adapt_report(framework, native, artifact_root, run_dir, runner):
    if framework not in {"pytest-junit", "playwright-json"}:
        raise ValueError("framework must be pytest-junit or playwright-json")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", runner):
        raise ValueError("runner id must use letters, digits, underscore or hyphen")
    native, artifact_root, run_dir = Path(native).resolve(), Path(artifact_root).resolve(), Path(run_dir).resolve()
    if not native.is_file() or native.stat().st_size == 0 or not artifact_root.is_dir() or not run_dir.is_dir():
        raise ValueError("native report, artifact root and run directory must exist")
    parsed = _pytest_cases(native) if framework == "pytest-junit" else _playwright_cases(native)
    ids = [case["id"] for case in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError("native report contains duplicate normalized test IDs")
    native_rel = f"native/{runner}{native.suffix.lower()}"
    obs_rel = f"observations/{runner}.json"
    manifest_rel = f"evidence/{runner}.json"
    for rel in (native_rel, obs_rel, manifest_rel):
        if (run_dir / rel).exists():
            raise ValueError(f"run artifact already exists: {rel}")
    artifacts = []

    def record(path, rel):
        artifacts.append({"path": rel, "sha256": _hash(path), "size": path.stat().st_size})
        return rel

    native_dest = run_dir / native_rel
    native_dest.parent.mkdir(parents=True, exist_ok=True)
    with native.open("rb") as source, native_dest.open("xb") as target:
        shutil.copyfileobj(source, target)
    record(native_dest, native_rel)
    normalized = []
    serial = 0
    for case in parsed:
        row = {"id": case["id"], "status": case["status"], "flaky": case["flaky"],
               "evidence": [native_rel], "interaction_evidence": [], "runtime_errors_checked": False,
               "unexpected_errors": [], "issues": []}
        if framework == "playwright-json":
            for attachment in case["attachments"]:
                if not isinstance(attachment, dict):
                    row["issues"].append("原生附件格式无效")
                    continue
                serial += 1
                name = str(attachment.get("name") or "attachment")
                source_name = Path(str(attachment.get("path") or name)).name
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", source_name)[:80] or "attachment"
                rel = f"artifacts/{runner}/{serial:05d}-{safe_name}"
                destination = run_dir / rel
                if attachment.get("path"):
                    source = Path(attachment["path"])
                    source = (artifact_root / source).resolve() if not source.is_absolute() else source.resolve()
                    try:
                        source.relative_to(artifact_root)
                        allowed = source.is_file() and source.stat().st_size > 0
                    except (ValueError, OSError):
                        allowed = False
                    if not allowed:
                        row["issues"].append(f"附件缺失或越过声明目录：{name}")
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.open("rb") as input_file, destination.open("xb") as output_file:
                        shutil.copyfileobj(input_file, output_file)
                elif isinstance(attachment.get("body"), str):
                    body = attachment["body"]
                    try:
                        content = base64.b64decode(body, validate=True)
                    except (ValueError, base64.binascii.Error):
                        content = body.encode("utf-8")
                    if not content:
                        row["issues"].append(f"附件内容为空：{name}")
                        continue
                    _write(destination, content)
                else:
                    row["issues"].append(f"附件没有可读取内容：{name}")
                    continue
                record(destination, rel)
                row["evidence"].append(rel)
                if "trace" in name.lower() or name == "project-check-actions":
                    row["interaction_evidence"].append(rel)
                if name == "project-check-browser-events":
                    try:
                        event_log = json.loads(destination.read_text(encoding="utf-8"))
                        if event_log.get("version") != 1 or event_log.get("checked") is not True or not isinstance(event_log.get("unexpected"), list):
                            raise ValueError("invalid browser event log")
                        row["runtime_errors_checked"] = True
                        row["unexpected_errors"].extend(str(value) for value in event_log["unexpected"])
                    except (ValueError, UnicodeError, TypeError) as error:
                        row["issues"].append(f"浏览器事件记录无效：{error}")
            steps = [result.get("steps", []) for result in case["results"]]
            if any(steps):
                serial += 1
                rel = f"artifacts/{runner}/{serial:05d}-steps.json"
                _write(run_dir / rel, _json_bytes(steps))
                record(run_dir / rel, rel)
                row["interaction_evidence"].append(rel)
        normalized.append(row)
    observation = {"version": 1, "framework": framework, "runner": runner,
                   "native_report": native_rel, "cases": normalized}
    _write(run_dir / obs_rel, _json_bytes(observation))
    record(run_dir / obs_rel, obs_rel)
    _write(run_dir / manifest_rel, _json_bytes({"version": 1, "runner": runner, "artifacts": artifacts}))
    return {"native_report": native_rel, "observations": obs_rel, "evidence_manifest": manifest_rel,
            "cases": len(normalized), "issues": sum(len(row["issues"]) for row in normalized)}
