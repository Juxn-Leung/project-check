#!/usr/bin/env python3
"""Validate Project Check inventories and summarize observed test evidence.

This tool does not start services or tests. finish may execute the configured OpenSpec CLI under saved policy.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

# Also supports loading this module through importlib from external test harnesses.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


STATUSES = {"passed", "failed", "blocked", "skipped"}
KINDS = {"unit", "integration", "browser", "e2e", "visual"}
MODES = {"real", "mocked", "none"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value, label, allow_empty=True):
    require(isinstance(value, list), f"{label}: expected list")
    require(all(nonempty(x) for x in value), f"{label}: expected nonempty strings")
    require(allow_empty or bool(value), f"{label}: cannot be empty")
    return value


def records(value, label):
    require(isinstance(value, list), f"{label}: expected list")
    seen = set()
    for item in value:
        require(isinstance(item, dict) and nonempty(item.get("id")), f"{label}: missing id")
        require(item["id"] not in seen, f"{label}: duplicate id {item['id']}")
        seen.add(item["id"])
    return {item["id"]: item for item in value}


def read_json(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


def validate_inventory(data):
    require(isinstance(data, dict) and data.get("version") in {1, 2}, "inventory: version must be 1 or 2")
    modules = records(data.get("modules"), "modules")
    for key, item in modules.items():
        require(nonempty(item.get("name")), f"{key}: missing module name")
        deps = strings(item.get("regression_dependencies", []), key + ": dependencies")
        require(set(deps) <= modules.keys(), f"{key}: unknown regression dependency")
    if data["version"] == 2:
        sources = records(data.get("requirement_sources"), "requirement_sources")
        for key, source in sources.items():
            require(source.get("kind") in {"file", "external"}, f"{key}: invalid source kind")
            require(type(source.get("reviewed")) is bool, f"{key}: reviewed must be boolean")
            source_modules = strings(source.get("modules"), key + ": modules", False)
            require(len(source_modules) == len(set(source_modules)) and set(source_modules) <= modules.keys(),
                    f"{key}: invalid source modules")
            if source["kind"] == "file":
                path = source.get("path")
                require(nonempty(path) and not Path(path).is_absolute() and ".." not in Path(path).parts,
                        f"{key}: invalid source path")
                require(isinstance(source.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", source["sha256"]),
                        f"{key}: invalid source sha256")
            else:
                require(nonempty(source.get("locator")) and nonempty(source.get("revision")),
                        f"{key}: external source needs locator and revision")
        requirements = records(data.get("requirements"), "requirements")
        for key, item in requirements.items():
            require(item.get("module") in modules and nonempty(item.get("title")), f"{key}: invalid requirement")
            require(item.get("source") in sources, f"{key}: unknown requirement source")
            require(item["module"] in sources[item["source"]]["modules"], f"{key}: source does not cover module")
            require(item.get("state") in {"active", "retired"}, f"{key}: invalid requirement state")
            if item["state"] == "retired":
                require(nonempty(item.get("retirement_reason")), f"{key}: retirement needs reason")
    else:
        sources, requirements = {}, {}
    scenarios = records(data.get("scenarios"), "scenarios")
    for key, item in scenarios.items():
        require(item.get("module") in modules, f"{key}: unknown module")
        require(nonempty(item.get("title")), f"{key}: missing title")
        require(item.get("state") in {"active", "retired"}, f"{key}: invalid state")
        if item["state"] == "retired":
            require(nonempty(item.get("retirement_reason")), f"{key}: retirement needs reason")
            continue
        if data["version"] == 2:
            refs = strings(item.get("requirement_ids"), key + ": requirement_ids")
            require(len(refs) == len(set(refs)), f"{key}: duplicate requirement mapping")
            require(set(refs) <= requirements.keys(), f"{key}: unknown requirement mapping")
            require(all(requirements[ref]["state"] == "active" for ref in refs), f"{key}: maps retired requirement")
            require(all(requirements[ref]["module"] == item["module"] for ref in refs),
                    f"{key}: cross-module requirement mapping")
        require(item.get("kind") in KINDS, f"{key}: invalid kind")
        require(item.get("coverage") in {"implemented", "missing"}, f"{key}: invalid coverage")
        basis = item.get("basis")
        require(isinstance(basis, dict), f"{key}: missing basis")
        require(basis.get("status") in {"confirmed", "inferred", "unknown"}, f"{key}: invalid basis")
        require(nonempty(basis.get("source")), f"{key}: missing basis source")
        strings(item.get("preconditions"), key + ": preconditions")
        strings(item.get("actions"), key + ": actions", False)
        strings(item.get("expected"), key + ": expected", False)
        tests = strings(item.get("tests"), key + ": tests")
        require(len(tests) == len(set(tests)), f"{key}: duplicate test mappings")
        if item["coverage"] == "implemented":
            require(tests and nonempty(item.get("runner")), f"{key}: implemented requires tests and runner")
        require(item.get("dependency_mode") in MODES, f"{key}: invalid dependency_mode")
        require(item["kind"] != "e2e" or item["dependency_mode"] == "real", f"{key}: e2e requires real dependencies")
        if "checks" in item:
            from acceptance import validate_checks
            validate_checks(item["checks"], tests)
    return data


def validate_project(project, inventory):
    require(isinstance(project, dict) and project.get("version") == 1, "project: version must be 1")
    require(nonempty(project.get("name")), "project: missing name")
    strings(project.get("required_env"), "required_env")
    services = records(project.get("services"), "services")
    runners = records(project.get("runners"), "runners")
    for key, service in services.items():
        strings(service.get("start"), key + ": start", False)
        for field in ("cwd", "ready_url", "identity_check"):
            require(nonempty(service.get(field)), f"{key}: missing {field}")
        require(type(service.get("timeout_seconds")) in (int, float) and service["timeout_seconds"] > 0,
                f"{key}: timeout_seconds must be positive")
        deps = strings(service.get("depends_on", []), key + ": depends_on")
        require(set(deps) <= services.keys(), f"{key}: unknown service dependency")
    visiting, done = set(), set()

    def visit(key):
        require(key not in visiting, f"{key}: cyclic service dependencies")
        if key in done:
            return
        visiting.add(key)
        for dep in services[key].get("depends_on", []):
            visit(dep)
        visiting.remove(key)
        done.add(key)

    for key in services:
        visit(key)
    for field in ("data_setup", "data_cleanup"):
        require(isinstance(project.get(field), list), f"{field}: expected list")
        for command in project[field]:
            require(isinstance(command, dict) and nonempty(command.get("cwd")), f"{field}: missing cwd")
            strings(command.get("argv"), field + ": argv", False)
    for key, runner in runners.items():
        for field in ("cwd", "selection", "native_report"):
            require(nonempty(runner.get(field)), f"{key}: missing {field}")
        for field in ("argv", "discovery"):
            strings(runner.get(field), key + ": " + field, False)
        deps = strings(runner.get("requires_services", []), key + ": requires_services")
        require(set(deps) <= services.keys(), f"{key}: unknown required service")
    for item in inventory["scenarios"]:
        if item["state"] == "active" and item["coverage"] == "implemented":
            require(item["runner"] in runners, f"{item['id']}: unknown runner")


def scope_modules(inventory, module=None):
    modules = {x["id"]: x for x in inventory["modules"]}
    if module is None:
        chosen, scope = set(modules), "all"
    else:
        matches = [module] if module in modules else [key for key, value in modules.items() if value["name"] == module]
        require(len(matches) == 1, f"Unknown or ambiguous module: {module}")
        chosen, pending, scope = set(), matches[:], "module:" + matches[0]
        while pending:
            key = pending.pop()
            if key not in chosen:
                chosen.add(key)
                pending.extend(modules[key].get("regression_dependencies", []))
    return scope, chosen


def select(inventory, module=None):
    scope, chosen = scope_modules(inventory, module)
    items = [x for x in inventory["scenarios"] if x["state"] == "active" and x["module"] in chosen]
    return scope, items


def evidence_exists(value, base):
    if not nonempty(value) or Path(value).is_absolute():
        return False
    path = (base / value).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return False
    return path.is_file() and path.stat().st_size > 0


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_review(inventory, root):
    """Capture source state at run start; old reports use this frozen observation."""
    rows = []
    if inventory["version"] != 2:
        return rows
    root = Path(root).resolve()
    for source in inventory["requirement_sources"]:
        row = {"id": source["id"], "reviewed": source["reviewed"], "matches": True}
        if source["kind"] == "file":
            path = (root / source["path"]).resolve()
            try:
                path.relative_to(root)
                actual = sha256(path) if path.is_file() else None
            except (ValueError, OSError):
                actual = None
            row["actual_sha256"] = actual
            row["matches"] = actual == source["sha256"]
        else:
            row["revision"] = source["revision"]
        rows.append(row)
    return rows


def source_current(source, observed):
    return bool(source["reviewed"] and observed and observed.get("reviewed") is True and
                observed.get("matches") is True and
                (source["kind"] != "file" or observed.get("actual_sha256") == source["sha256"]) and
                (source["kind"] != "external" or observed.get("revision") == source["revision"]))


def requirement_gaps(inventory, chosen, review):
    gaps, coverage = [], []
    if inventory["version"] == 1:
        return [f"{module}: 旧清单尚未登记和审阅需求。" for module in sorted(chosen)], coverage
    requirements = [x for x in inventory["requirements"] if x["state"] == "active" and x["module"] in chosen]
    sources = {x["id"]: x for x in inventory["requirement_sources"]}
    reviews = {x["id"]: x for x in review if isinstance(x, dict) and nonempty(x.get("id"))}

    for source in sources.values():
        if chosen.intersection(source["modules"]) and not source_current(source, reviews.get(source["id"])):
            gaps.append(f"{source['id']}: 需求来源未复核、已变化或缺少运行快照。")
    scenarios = [x for x in inventory["scenarios"] if x["state"] == "active"]
    for scenario in scenarios:
        if scenario["module"] in chosen and not scenario["requirement_ids"]:
            gaps.append(f"{scenario['id']}: 活动场景未关联已登记需求。")
    for module in sorted(chosen):
        module_reqs = [x for x in requirements if x["module"] == module]
        if not module_reqs:
            gaps.append(f"{module}: 没有已登记的有效需求，不能确认模块需求完整。")
        for req in module_reqs:
            mapped = [x["id"] for x in scenarios if x["module"] == module and req["id"] in x["requirement_ids"]]
            source = sources[req["source"]]
            reviewed = source_current(source, reviews.get(source["id"]))
            if not mapped:
                gaps.append(f"{req['id']}: 有效需求未映射到活动场景。")
            coverage.append({"id": req["id"], "module": module, "title": req["title"],
                             "scenarios": mapped, "source_reviewed": reviewed})
    return gaps, coverage


def verified_manifest(path, base):
    if not evidence_exists(path, base):
        return {}, [f"证据清单缺失：{path}"]
    try:
        data = read_json(base / path)
        require(isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("artifacts"), list),
                "invalid evidence manifest")
        entries = {}
        problems = []
        for row in data["artifacts"]:
            require(isinstance(row, dict) and nonempty(row.get("path")) and
                    isinstance(row.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]),
                    "invalid evidence manifest entry")
            require(row["path"] not in entries, "duplicate evidence manifest path")
            entries[row["path"]] = row
            if not evidence_exists(row["path"], base) or sha256(base / row["path"]) != row["sha256"]:
                problems.append(f"证据缺失或哈希不符：{row['path']}")
        return entries, problems
    except (ValueError, TypeError, OSError, KeyError) as error:
        return {}, [f"证据清单无效：{error}"]


def artifact_ok(path, base, entries):
    return path in entries and evidence_exists(path, base) and sha256(base / path) == entries[path]["sha256"]


def timestamp(value, label):
    require(nonempty(value), f"run: missing {label}")
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(time.utcoffset() is not None, f"run: {label} requires timezone")
    return time


def summarize(inventory, payload, base, module=None):
    validate_inventory(inventory)
    scope, selected = select(inventory, module)
    require(isinstance(payload, dict) and payload.get("version") in {1, 2}, "results: version must be 1 or 2")
    run = payload.get("run")
    require(isinstance(run, dict), "results: missing run")
    for field in ("id", "revision", "workspace", "environment"):
        require(nonempty(run.get(field)), f"run: missing {field}")
    require(timestamp(run.get("finished_at"), "finished_at") >= timestamp(run.get("started_at"), "started_at"),
            "run: finish precedes start")
    require(run.get("scope") == scope, "run scope does not match selected scope")
    expected_ids = {x["id"] for x in selected}
    ids = strings(run.get("selected_ids"), "run.selected_ids")
    require(len(ids) == len(set(ids)) and set(ids) == expected_ids, "run selected_ids mismatch")
    rows = records(payload.get("results"), "results")
    require(rows.keys() <= expected_ids, "results contain unknown or out-of-scope IDs")
    native = strings(run.get("native_reports"), "run.native_reports")
    gaps = []
    if not selected:
        gaps.append("零场景：没有可执行验收范围。")
    _, chosen_modules = scope_modules(inventory, module)
    for empty_module in sorted(chosen_modules - {x["module"] for x in selected}):
        gaps.append(f"{empty_module}: 模块尚无活动验收场景。")
    manifests, manifest_problems, observations = {}, [], {}
    snapshot_ok = False
    observations_ok = True
    if payload["version"] == 2:
        manifest_paths = [run.get("snapshot_manifest")] + strings(run.get("evidence_manifests"), "run.evidence_manifests", False)
        observation_paths = strings(run.get("observations"), "run.observations", False)
        for manifest_path in manifest_paths:
            entries, problems = verified_manifest(manifest_path, base)
            for path, entry in entries.items():
                require(path not in manifests, f"duplicate frozen artifact: {path}")
                manifests[path] = entry
            manifest_problems.extend(problems)
        gaps.extend(manifest_problems)
        snapshot_ok = all(artifact_ok(path, base, manifests) for path in
                          ("inventory.json", "project.json", run.get("source_review")))
        for snapshot_path in ("inventory.json", "project.json", run.get("source_review")):
            if not artifact_ok(snapshot_path, base, manifests):
                gaps.append(f"运行快照缺失或哈希不符：{snapshot_path}")
        for observation_path in observation_paths:
            if not evidence_exists(observation_path, base) or observation_path not in manifests or not artifact_ok(observation_path, base, manifests):
                gaps.append(f"适配器结果缺失或哈希不符：{observation_path}")
                observations_ok = False
                continue
            observation = read_json(base / observation_path)
            require(isinstance(observation, dict) and observation.get("version") == 1 and
                    isinstance(observation.get("cases"), list),
                    f"invalid observations: {observation_path}")
            for case in observation["cases"]:
                require(isinstance(case, dict) and nonempty(case.get("id")) and case.get("status") in STATUSES,
                        f"invalid observed case: {observation_path}")
                require(case["id"] not in observations, f"duplicate observed case: {case['id']}")
                observations[case["id"]] = case
    else:
        gaps.append("旧版结果没有冻结证据哈希，不能确认模块验收完整。")
    review = []
    if payload["version"] == 2 and artifact_ok(run.get("source_review"), base, manifests):
        review_data = read_json(base / run["source_review"])
        require(isinstance(review_data, dict) and review_data.get("version") == 1 and
                isinstance(review_data.get("sources"), list), "invalid source review snapshot")
        review = review_data["sources"]
    mapping_gaps, requirement_coverage = requirement_gaps(inventory, chosen_modules, review)
    gaps.extend(mapping_gaps)
    native_ok = bool(native) and all(evidence_exists(x, base) and
                                    (payload["version"] == 1 or artifact_ok(x, base, manifests)) for x in native)
    if not native_ok:
        gaps.append("原生测试报告缺失、为空或路径无效，无法完整核验执行。")
    evaluated = []
    for scenario in selected:
        key = scenario["id"]
        if scenario["coverage"] == "missing":
            gaps.append(f"{key}: 测试尚未实现。")
        if scenario["basis"]["status"] != "confirmed":
            gaps.append(f"{key}: 预期依据为 {scenario['basis']['status']}，尚未确认。")
        row = rows.get(key)
        if row is None:
            evaluated.append({"id": key, "title": scenario["title"], "status": "blocked", "reason": "没有执行结果。", "evidence": []})
            continue
        status = row.get("status")
        require(status in STATUSES, f"{key}: invalid result status")
        require(status == "passed" or nonempty(row.get("reason")), f"{key}: non-pass requires reason")
        evidence = list(strings(row.get("evidence", []), key + ": evidence"))
        interaction = list(strings(row.get("interaction_evidence", []), key + ": interaction_evidence"))
        errors = list(strings(row.get("unexpected_errors", []), key + ": unexpected_errors"))
        if payload["version"] == 2:
            require(not any(field in row for field in ("observed_tests", "runtime_errors_checked", "flaky", "checks")),
                    f"{key}: native observation fields must come from the adapter")
            cases = [observations[test_id] for test_id in scenario["tests"] if test_id in observations]
            observed = {x["id"]: x for x in cases}
            for case in cases:
                evidence.extend(strings(case.get("evidence", []), key + ": adapter evidence"))
                interaction.extend(strings(case.get("interaction_evidence", []), key + ": adapter interaction evidence"))
                errors.extend(strings(case.get("unexpected_errors", []), key + ": adapter errors"))
        else:
            require(type(row.get("flaky", False)) is bool, f"{key}: flaky must be boolean")
            observed = records(row.get("observed_tests", []), key + ": observed_tests")
        require(all(x.get("status") in STATUSES for x in observed.values()), f"{key}: invalid observed status")
        for flag in (("assertions_checked",) if payload["version"] == 2 else ("assertions_checked", "runtime_errors_checked")):
            require(flag not in row or type(row[flag]) is bool, f"{key}: {flag} must be boolean")
        reasons = [row["reason"]] if nonempty(row.get("reason")) else []
        flaky = (payload["version"] == 1 and row.get("flaky") is True) or any(
            x.get("flaky") is True for x in observed.values())
        if errors or flaky or any(x["status"] == "failed" for x in observed.values()):
            status = "failed"
            reasons.extend(errors)
            if flaky:
                reasons.append("重试后通过或结果不稳定，不能作为稳定通过。")
            if any(x["status"] == "failed" for x in observed.values()):
                reasons.append("原生子用例存在失败。")
        if status == "passed":
            missing = []
            if scenario["coverage"] != "implemented":
                missing.append("未建立可执行覆盖")
            if set(observed) != set(scenario["tests"]) or not observed or any(x["status"] != "passed" for x in observed.values()):
                missing.append("必需原生用例未全部发现、执行并通过")
            if not native_ok:
                missing.append("缺少有效原生报告")
            if not evidence or not all(evidence_exists(x, base) and
                                       (payload["version"] == 1 or artifact_ok(x, base, manifests)) for x in evidence):
                missing.append("执行证据缺失或无效")
            if payload["version"] == 2 and any(x.get("issues") for x in observed.values()):
                missing.append("原生附件未能完整冻结")
            if row.get("assertions_checked") is not True:
                missing.append("未核对结果断言")
            if row.get("dependency_mode") != scenario["dependency_mode"]:
                missing.append("实际依赖模式与场景要求不符")
            if scenario["kind"] in {"browser", "e2e", "visual"}:
                if payload["version"] == 2:
                    from acceptance import missing_checks
                    missing.extend(missing_checks(scenario, observed))
                checked = (all(x.get("runtime_errors_checked") is True for x in observed.values())
                           if payload["version"] == 2 else row.get("runtime_errors_checked") is True)
                if not checked:
                    missing.append("没有运行错误检查证据")
                if not interaction or not all(evidence_exists(x, base) and
                                              (payload["version"] == 1 or artifact_ok(x, base, manifests)) for x in interaction):
                    missing.append("没有浏览器操作/断言轨迹")
            if missing:
                status = "blocked"
                reasons.extend(missing)
        if status != "passed" and (not evidence or not all(evidence_exists(x, base) for x in evidence + interaction)):
            gaps.append(f"{key}: 非通过结果的诊断证据不完整。")
        evaluated.append({"id": key, "title": scenario["title"], "status": status,
                          "reason": "；".join(reasons), "evidence": list(dict.fromkeys(evidence + interaction))})
    counts = {status: sum(x["status"] == status for x in evaluated) for status in sorted(STATUSES)}
    outcome = "FAILED" if counts["failed"] else ("INCOMPLETE" if gaps or counts["blocked"] or counts["skipped"] else "PASSED")
    scenario_modules = {x["id"]: x["module"] for x in selected}
    modules = []
    global_evidence_ok = payload["version"] == 2 and snapshot_ok and observations_ok and native_ok and not manifest_problems
    review_by_id = {x["id"]: x for x in review}
    for module_id in sorted(chosen_modules):
        module_results = [x for x in evaluated if scenario_modules[x["id"]] == module_id]
        module_reqs = [x for x in requirement_coverage if x["module"] == module_id]
        mappings_ok = bool(module_reqs) and all(x["scenarios"] and x["source_reviewed"] for x in module_reqs)
        sources_ok = payload["version"] == 2 and all(
            source_current(source, review_by_id.get(source["id"]))
            for source in inventory["requirement_sources"] if module_id in source["modules"])
        module_scenarios = [x for x in selected if x["module"] == module_id]
        scenario_basis_ok = all(x["coverage"] == "implemented" and x["basis"]["status"] == "confirmed" and
                                bool(x.get("requirement_ids"))
                                for x in module_scenarios)
        module_outcome = ("FAILED" if any(x["status"] == "failed" for x in module_results) else
                          "PASSED" if global_evidence_ok and mappings_ok and sources_ok and scenario_basis_ok and module_results and
                          all(x["status"] == "passed" for x in module_results) else "INCOMPLETE")
        modules.append({"id": module_id, "outcome": module_outcome, "requirements": len(module_reqs),
                        "mapped_requirements": sum(bool(x["scenarios"]) for x in module_reqs)})
    return {"run": run, "outcome": outcome, "counts": counts, "gaps": gaps,
            "requirements": requirement_coverage, "modules": modules, "results": evaluated}


def cell(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def render_report(summary):
    run = summary["run"]
    lines = [f"# Project Check · {cell(run['id'])}", "", f"结果：**{summary['outcome']}**", "",
             "此结果仅针对所选清单；文件存在校验不替代原生报告内容审阅。", ""]
    for field in ("scope", "revision", "workspace", "environment", "started_at", "finished_at"):
        lines.append(f"- {field}: {cell(run[field])}")
    lines += ["", " | ".join(f"{k}: {v}" for k, v in summary["counts"].items()), "",
              "| 场景 | 标题 | 状态 | 原因 | 证据（相对 results.json） |",
              "| --- | --- | --- | --- | --- |"]
    for row in summary["results"]:
        lines.append("| " + " | ".join(cell(row[x]) for x in ("id", "title", "status", "reason")) + " | " + cell(", ".join(row["evidence"])) + " |")
    lines += ["", "## 已登记需求映射", "", "此表仅反映已登记并审阅的需求来源；不能证明未知需求不存在。", "",
              "| 需求 | 模块 | 标题 | 活动场景 | 来源已复核 |", "| --- | --- | --- | --- | --- |"]
    for row in summary["requirements"]:
        lines.append("| " + " | ".join(cell(value) for value in
                     (row["id"], row["module"], row["title"], ", ".join(row["scenarios"]),
                      "是" if row["source_reviewed"] else "否")) + " |")
    lines += ["", "## 模块结论", "", "| 模块 | 结论 | 有效需求 | 已映射 |",
              "| --- | --- | --- | --- |"]
    for row in summary["modules"]:
        lines.append(f"| {cell(row['id'])} | {row['outcome']} | {row['requirements']} | {row['mapped_requirements']} |")
    lines += ["", "## 覆盖与证据缺口", ""]
    lines += ["- " + cell(x) for x in summary["gaps"]] or ["当前所选清单没有已记录的缺口；不代表未知功能已被覆盖。"]
    lines += ["", "## 原生报告", ""] + ["- " + cell(x) for x in run["native_reports"]]
    return "\n".join(lines) + "\n"


def render_checklist(inventory):
    lines = ["# Project Check · 测试清单", "", "由 inventory.json 生成，请修改源清单后重新生成。", "",
             "| ID | 模块 | 场景 | 类型 | 状态 | 覆盖 | 依据 | 测试映射 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for item in inventory["scenarios"]:
        values = [item["id"], item["module"], item["title"], item.get("kind", ""), item["state"], item.get("coverage", ""),
                  item.get("basis", {}).get("status", ""), ", ".join(item.get("tests", []))]
        lines.append("| " + " | ".join(cell(x) for x in values) + " |")
    if inventory["version"] == 2:
        lines += ["", "## 需求到场景", "", "| 需求 | 模块 | 标题 | 来源 | 活动场景 |",
                  "| --- | --- | --- | --- | --- |"]
        for req in inventory["requirements"]:
            mapped = [x["id"] for x in inventory["scenarios"] if x["state"] == "active" and
                      req["id"] in x.get("requirement_ids", [])]
            lines.append("| " + " | ".join(cell(x) for x in
                         (req["id"], req["module"], req["title"], req["source"], ", ".join(mapped))) + " |")
    for item in inventory["scenarios"]:
        lines += ["", "## " + cell(item["id"]) + " · " + cell(item["title"]), ""]
        for field, label in (("preconditions", "前提"), ("actions", "操作"), ("expected", "预期")):
            lines += [f"{label}：", ""] + ["- " + cell(x) for x in item.get(field, [])] + [""]
        lines.append("依据：" + cell(item.get("basis", {}).get("source", item.get("retirement_reason", ""))))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "validate", "checklist", "select", "snapshot", "adapt", "report",
                                           "map", "baseline", "review", "seal", "finish", "freshness"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--module", help="Module ID or unique display name (select/report/seal)")
    parser.add_argument("--inventory", type=Path, help="Frozen inventory snapshot (report only)")
    parser.add_argument("--results", type=Path, help="Observed results JSON (report only)")
    parser.add_argument("--out", type=Path, help="New report file; existing files are not overwritten")
    parser.add_argument("--run-dir", type=Path, help="Run directory (snapshot/adapt/map/review/seal/finish/freshness)")
    parser.add_argument("--change-id", help="Stable change ID (baseline/review/finish)")
    parser.add_argument("--notes", type=Path, help="Agent explanations JSON (review)")
    parser.add_argument("--execute", action="store_true", help="Archive after validation under saved policy (finish)")
    parser.add_argument("--framework", choices=("pytest-junit", "playwright-json"), help="Native report format (adapt)")
    parser.add_argument("--native", type=Path, help="Native report file (adapt)")
    parser.add_argument("--artifact-root", type=Path, help="Allowed attachment directory (adapt)")
    parser.add_argument("--runner", help="Runner ID (adapt)")
    args = parser.parse_args(argv)
    try:
        require(args.root.is_dir(), "Project root does not exist")
        require(not args.module or args.command in {"select", "report", "seal"}, "--module is only valid for select/report/seal")
        require(not (args.inventory or args.results or args.out) or args.command == "report", "--inventory/--results/--out are report-only")
        require(not args.run_dir or args.command in {"snapshot", "adapt", "map", "review", "seal", "finish", "freshness"},
                "--run-dir is not supported by this command")
        require(not args.change_id or args.command in {"baseline", "review", "finish"}, "--change-id is baseline/review/finish-only")
        require(not args.notes or args.command == "review", "--notes is review-only")
        require(not args.execute or args.command == "finish", "--execute is finish-only")
        require(not (args.framework or args.native or args.artifact_root or args.runner) or args.command == "adapt",
                "adapter options are adapt-only")
        config = args.root / ".project-check"
        if args.command in {"map", "baseline", "review"}:
            from project_model import handle
            result = handle(args.command, args.root, change_id=args.change_id, run_dir=args.run_dir, notes=args.notes)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"seal", "finish", "freshness"}:
            require(args.run_dir is not None, f"{args.command} requires --run-dir")
            if args.command == "seal":
                from completion import seal
                result = seal(args.root, args.run_dir, args.module)
            elif args.command == "finish":
                from completion import finish
                require(args.change_id is not None, "finish requires --change-id")
                result = finish(args.root, args.run_dir, args.change_id, execute=args.execute)
            else:
                from workspace_state import current
                entries, problems = verified_manifest("snapshot-manifest.json", args.run_dir)
                require(not problems and artifact_ok("workspace.json", args.run_dir, entries),
                        "Source snapshot is missing or invalid; start a new run")
                result = current(read_json(args.run_dir / "workspace.json"), args.root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result.get("outcome") == "BLOCKED" or result.get("current") is False else 0
        if args.command == "init":
            config.mkdir(exist_ok=True)
            assets = Path(__file__).resolve().parent.parent / "assets"
            for name in ("project", "inventory"):
                path = config / (name + ".json")
                if not path.exists():
                    data = read_json(assets / (name + ".template.json"))
                    if name == "project":
                        data["name"] = args.root.resolve().name
                    with path.open("x", encoding="utf-8") as file:
                        file.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            print(f"Initialized missing configuration in {config}; populate before running tests.")
            return 0
        inventory = validate_inventory(read_json(args.inventory or config / "inventory.json"))
        if args.command not in {"report", "adapt"}:
            validate_project(read_json(config / "project.json"), inventory)
        if args.command == "validate":
            active = [x for x in inventory["scenarios"] if x["state"] == "active"]
            review = source_review(inventory, args.root)
            chosen = {x["id"] for x in inventory["modules"]}
            mapping_gaps, requirement_coverage = requirement_gaps(inventory, chosen, review)
            print(json.dumps({"valid": True, "active_scenarios": len(active), "coverage": dict(Counter(x["coverage"] for x in active)),
                              "requirements": len(requirement_coverage), "requirement_gaps": mapping_gaps,
                              "note": "Structure validation is not a test run."}, ensure_ascii=False))
        elif args.command == "checklist":
            path = config / "CHECKLIST.md"
            path.write_text(render_checklist(inventory), encoding="utf-8")
            print(path)
        elif args.command == "select":
            scope, items = select(inventory, args.module)
            print(json.dumps({"scope": scope, "selected_ids": [x["id"] for x in items]}, ensure_ascii=False, indent=2))
        elif args.command == "snapshot":
            require(args.run_dir is not None, "snapshot requires --run-dir")
            from workspace_state import capture
            workspace = capture(args.root)
            args.run_dir.mkdir(parents=True, exist_ok=False)
            (args.run_dir / "workspace.json").write_text(json.dumps(workspace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            shutil.copyfile(config / "inventory.json", args.run_dir / "inventory.json")
            shutil.copyfile(config / "project.json", args.run_dir / "project.json")
            with (args.run_dir / "source-review.json").open("x", encoding="utf-8") as file:
                json.dump({"version": 1, "sources": source_review(inventory, args.root)}, file, ensure_ascii=False, indent=2)
                file.write("\n")
            snapshot_artifacts = [{"path": name, "sha256": sha256(args.run_dir / name),
                                   "size": (args.run_dir / name).stat().st_size}
                                  for name in ("inventory.json", "project.json", "source-review.json", "workspace.json")]
            with (args.run_dir / "snapshot-manifest.json").open("x", encoding="utf-8") as file:
                json.dump({"version": 1, "artifacts": snapshot_artifacts}, file, ensure_ascii=False, indent=2)
                file.write("\n")
            print(args.run_dir)
        elif args.command == "adapt":
            require(all((args.run_dir, args.framework, args.native, args.artifact_root, args.runner)),
                    "adapt requires --run-dir, --framework, --native, --artifact-root and --runner")
            from report_adapters import adapt_report
            result = adapt_report(args.framework, args.native, args.artifact_root, args.run_dir, args.runner)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            require(args.results is not None, "report requires --results")
            if read_json(args.results).get("version") == 2:
                require(args.inventory is not None and args.inventory.resolve() == (args.results.resolve().parent / "inventory.json"),
                        "v2 report requires the run's frozen inventory.json")
            summary = summarize(inventory, read_json(args.results), args.results.resolve().parent, args.module)
            report = render_report(summary)
            if args.out:
                with args.out.open("x", encoding="utf-8") as file:
                    file.write(report)
                print(args.out)
            else:
                print(report, end="")
            return 0 if summary["outcome"] == "PASSED" else 1
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        print(f"project-check: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
