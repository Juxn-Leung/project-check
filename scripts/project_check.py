#!/usr/bin/env python3
"""Validate Project Check inventories and summarize observed test evidence.

This tool never starts services or executes commands stored in project.json.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys


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
    require(isinstance(data, dict) and data.get("version") == 1, "inventory: version must be 1")
    modules = records(data.get("modules"), "modules")
    for key, item in modules.items():
        require(nonempty(item.get("name")), f"{key}: missing module name")
        deps = strings(item.get("regression_dependencies", []), key + ": dependencies")
        require(set(deps) <= modules.keys(), f"{key}: unknown regression dependency")
    scenarios = records(data.get("scenarios"), "scenarios")
    for key, item in scenarios.items():
        require(item.get("module") in modules, f"{key}: unknown module")
        require(nonempty(item.get("title")), f"{key}: missing title")
        require(item.get("state") in {"active", "retired"}, f"{key}: invalid state")
        if item["state"] == "retired":
            require(nonempty(item.get("retirement_reason")), f"{key}: retirement needs reason")
            continue
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


def timestamp(value, label):
    require(nonempty(value), f"run: missing {label}")
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(time.utcoffset() is not None, f"run: {label} requires timezone")
    return time


def summarize(inventory, payload, base, module=None):
    validate_inventory(inventory)
    scope, selected = select(inventory, module)
    require(isinstance(payload, dict) and payload.get("version") == 1, "results: version must be 1")
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
    native_ok = bool(native) and all(evidence_exists(x, base) for x in native)
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
        evidence = strings(row.get("evidence", []), key + ": evidence")
        interaction = strings(row.get("interaction_evidence", []), key + ": interaction_evidence")
        errors = strings(row.get("unexpected_errors", []), key + ": unexpected_errors")
        require(type(row.get("flaky", False)) is bool, f"{key}: flaky must be boolean")
        observed = records(row.get("observed_tests", []), key + ": observed_tests")
        require(all(x.get("status") in STATUSES for x in observed.values()), f"{key}: invalid observed status")
        for flag in ("assertions_checked", "runtime_errors_checked"):
            require(flag not in row or type(row[flag]) is bool, f"{key}: {flag} must be boolean")
        reasons = [row["reason"]] if nonempty(row.get("reason")) else []
        if errors or row.get("flaky") or any(x["status"] == "failed" for x in observed.values()):
            status = "failed"
            reasons.extend(errors)
            if row.get("flaky"):
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
            if not evidence or not all(evidence_exists(x, base) for x in evidence):
                missing.append("执行证据缺失或无效")
            if row.get("assertions_checked") is not True:
                missing.append("未核对结果断言")
            if row.get("dependency_mode") != scenario["dependency_mode"]:
                missing.append("实际依赖模式与场景要求不符")
            if scenario["kind"] in {"browser", "e2e", "visual"}:
                if row.get("runtime_errors_checked") is not True:
                    missing.append("没有运行错误检查证据")
                if not interaction or not all(evidence_exists(x, base) for x in interaction):
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
    return {"run": run, "outcome": outcome, "counts": counts, "gaps": gaps, "results": evaluated}


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
    for item in inventory["scenarios"]:
        lines += ["", "## " + cell(item["id"]) + " · " + cell(item["title"]), ""]
        for field, label in (("preconditions", "前提"), ("actions", "操作"), ("expected", "预期")):
            lines += [f"{label}：", ""] + ["- " + cell(x) for x in item.get(field, [])] + [""]
        lines.append("依据：" + cell(item.get("basis", {}).get("source", item.get("retirement_reason", ""))))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "validate", "checklist", "select", "report"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--module", help="Module ID or unique display name (select/report)")
    parser.add_argument("--inventory", type=Path, help="Frozen inventory snapshot (report only)")
    parser.add_argument("--results", type=Path, help="Observed results JSON (report only)")
    parser.add_argument("--out", type=Path, help="New report file; existing files are not overwritten")
    args = parser.parse_args(argv)
    try:
        require(args.root.is_dir(), "Project root does not exist")
        require(not args.module or args.command in {"select", "report"}, "--module is only valid for select/report")
        require(not (args.inventory or args.results or args.out) or args.command == "report", "--inventory/--results/--out are report-only")
        config = args.root / ".project-check"
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
        if args.command != "report":
            validate_project(read_json(config / "project.json"), inventory)
        if args.command == "validate":
            active = [x for x in inventory["scenarios"] if x["state"] == "active"]
            print(json.dumps({"valid": True, "active_scenarios": len(active), "coverage": dict(Counter(x["coverage"] for x in active)), "note": "Structure validation is not a test run."}, ensure_ascii=False))
        elif args.command == "checklist":
            path = config / "CHECKLIST.md"
            path.write_text(render_checklist(inventory), encoding="utf-8")
            print(path)
        elif args.command == "select":
            scope, items = select(inventory, args.module)
            print(json.dumps({"scope": scope, "selected_ids": [x["id"] for x in items]}, ensure_ascii=False, indent=2))
        else:
            require(args.results is not None, "report requires --results")
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
