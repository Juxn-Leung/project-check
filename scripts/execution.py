"""Run configured local commands, freeze evidence, and resume at durable boundaries."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from urllib.request import urlopen
from uuid import uuid4

import project_check as pc
import workspace_state
from completion import immutable, seal
from report_adapters import adapt_report


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def execution_lock(run_dir):
    # OS locks are released even if the agent or interpreter is terminated.
    with (run_dir / ".execute.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError("Execution is already active") from error
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ValueError("Execution is already active") from error
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def inside(root, value, label):
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    pc.require(path.is_relative_to(root), label + ": path escapes project")
    return path


def expand(value, variables):
    # Replace only named tokens; braces in Python/JS arguments remain literal.
    for key, replacement in variables.items():
        value = value.replace("{" + key + "}", str(replacement))
    return value


def command_spec(root, item, variables, field="argv"):
    argv = pc.strings(item.get(field), field, False)
    cwd = inside(root, item.get("cwd", "."), "cwd")
    pc.require(cwd.is_dir(), "Command cwd does not exist: " + str(cwd))
    timeout = item.get("timeout_seconds", 120)
    pc.require(type(timeout) in (int, float) and 0 < timeout <= 86400, "Invalid command timeout")
    return [expand(arg, variables) for arg in argv], cwd, timeout


def stop(process):
    try:
        return _stop_owned_process(process)
    except PermissionError as error:
        # Permission loss during signalling or probing cannot prove cleanup.
        # Reap an exited leader and preserve the failure in the execution receipt.
        process.poll()
        return f"Could not confirm cleanup of owned process {process.pid}: {error}; inspect before retrying"


def _stop_owned_process(process):
    if os.name == "nt" and process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        pass
    if os.name != "nt":
        # The group leader can exit before a child that ignores SIGTERM.
        # Waiting on Popen alone does not prove the owned process group is gone.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return None
            time.sleep(0.02)
        return f"Owned process group {process.pid} still exists after cleanup; inspect before retrying"
    return None


def run_command(root, run_dir, state, key, item, variables, group="commands"):
    rows = state.setdefault(group, {})
    receipt_path = run_dir / "commands" / (key + ".json")
    previous = rows.get(key, {})
    if previous.get("phase") == "running":
        pc.require(receipt_path.is_file(),
                   f"Command interrupted with unknown outcome: {key}; inspect its process/log and start a new run")
    if receipt_path.is_file():
        receipt = pc.read_json(receipt_path)
        rows[key] = {"phase": "finished", "receipt": str(receipt_path.relative_to(run_dir))}
        atomic(run_dir / "execution.json", state)
        return receipt
    argv, cwd, timeout = command_spec(root, item, variables)
    receipt_path.parent.mkdir(exist_ok=True)
    log = run_dir / "commands" / (key + ".log")
    rows[key] = {"phase": "running", "started_at": now()}
    atomic(run_dir / "execution.json", state)
    receipt = {"argv": argv, "cwd": str(cwd), "started_at": rows[key]["started_at"],
               "exit_code": None, "error": None, "log": str(log.relative_to(run_dir))}
    environment = {**os.environ, "PROJECT_CHECK_RUN_DIR": str(run_dir), "PROJECT_CHECK_RUN_ID": run_dir.name}
    process = None
    try:
        with log.open("xb") as output:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
            rows[key]["pid"] = process.pid
            atomic(run_dir / "execution.json", state)
            try:
                receipt["exit_code"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                receipt["error"] = f"Command timed out after {timeout}s"
    except OSError as error:
        receipt["error"] = str(error)
    finally:
        if process is not None:
            cleanup_error = stop(process)
            if cleanup_error:
                receipt["error"] = cleanup_error
    receipt["finished_at"] = now()
    immutable(receipt_path, receipt)
    rows[key] = {"phase": "finished", "receipt": str(receipt_path.relative_to(run_dir))}
    atomic(run_dir / "execution.json", state)
    return receipt


class Services:
    def __init__(self, root, run_dir, project):
        self.root, self.run_dir = root, run_dir
        self.config = {s["id"]: s for s in project["services"]}
        self.started, self.errors = {}, {}

    def ensure(self, key):
        pc.require(os.name != "nt", "Managed service process-group cleanup requires macOS/Linux; use the test framework's service lifecycle on Windows")
        if key in self.errors:
            raise ValueError(self.errors[key])
        if key in self.started:
            pc.require(self.started[key][0].poll() is None, "Service exited: " + key)
            return
        service = self.config[key]
        try:
            for dependency in service.get("depends_on", []):
                self.ensure(dependency)
            pc.require(service.get("identity_argv"),
                       key + ": managed startup requires identity_argv to verify the actual service")
            variables = {"run_dir": self.run_dir, "run_id": self.run_dir.name}
            argv, cwd, timeout = command_spec(self.root, service, variables, "start")
            identity, identity_cwd, _ = command_spec(self.root, {**service, "argv": service["identity_argv"]}, variables)
            directory = self.run_dir / "services"
            directory.mkdir(exist_ok=True)
            log = (directory / (key + "-" + uuid4().hex[:8] + ".log")).open("xb")
            try:
                process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, "PROJECT_CHECK_RUN_DIR": str(self.run_dir), "PROJECT_CHECK_RUN_ID": self.run_dir.name},
                    start_new_session=os.name != "nt")
            except BaseException:
                log.close()
                raise
            self.started[key] = (process, log)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                pc.require(process.poll() is None, "Service exited during startup: " + key)
                try:
                    with urlopen(expand(service["ready_url"], variables), timeout=min(1, timeout)) as response:
                        ready = 200 <= response.status < 400
                    if ready:
                        result = subprocess.run(identity, cwd=identity_cwd, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, timeout=min(2, timeout),
                            env={**os.environ, "PROJECT_CHECK_RUN_DIR": str(self.run_dir), "PROJECT_CHECK_RUN_ID": self.run_dir.name})
                        if result.returncode == 0:
                            return
                except (OSError, subprocess.TimeoutExpired):
                    pass
                time.sleep(0.1)
            raise ValueError("Service readiness/identity timed out: " + key)
        except (OSError, ValueError) as error:
            self.errors[key] = str(error)
            raise ValueError(str(error)) from error

    def close(self):
        errors = []
        for process, log in reversed(list(self.started.values())):
            error = stop(process)
            if error:
                errors.append(error)
            log.close()
        return errors


def blocked_observation(run_dir, runner, reason):
    observation = {"version": 1, "runner": runner, "cases": [], "runner_status": "blocked",
                   "runner_errors": [reason], "native_report": None}
    path = "observations/" + runner + ".json"
    (run_dir / "observations").mkdir(exist_ok=True)
    (run_dir / "evidence").mkdir(exist_ok=True)
    immutable(run_dir / path, observation)
    manifest = "evidence/" + runner + ".json"
    immutable(run_dir / manifest, {"version": 1, "runner": runner, "artifacts": [
        {"path": path, "sha256": pc.sha256(run_dir / path), "size": (run_dir / path).stat().st_size}]})
    return {"observations": path, "evidence_manifest": manifest, "native_report": None}


def import_runner(run_dir, runner, native, artifacts):
    manifest = "evidence/" + runner["id"] + ".json"
    observed = "observations/" + runner["id"] + ".json"
    if (run_dir / manifest).is_file():
        entries, problems = pc.verified_manifest(manifest, run_dir)
        pc.require(not problems and pc.artifact_ok(observed, run_dir, entries), "Imported evidence changed")
        data = pc.read_json(run_dir / observed)
        return {"evidence_manifest": manifest, "observations": observed, "native_report": data.get("native_report")}
    return adapt_report(runner["framework"], native, artifacts, run_dir, runner["id"])


def reviewed_scenario(review, scenario, workspace):
    rows = {r["id"]: r for r in review.get("scenarios", [])}
    row = rows.get(scenario["id"], {})
    sources = row.get("test_sources", [])
    pc.strings(row.get("tests", []), "assessment tests")
    pc.strings(row.get("assertions", []), "assessment assertions")
    pc.require(isinstance(sources, list) and all(isinstance(s, dict) for s in sources), "Invalid test_sources")
    valid = (set(row.get("tests", [])) == set(scenario["tests"]) and bool(sources)
             and bool(row.get("assertions")) and all(isinstance(s, str) and s.strip() for s in row.get("assertions", []))
             and all(workspace["files"].get(source.get("path"), {}).get("sha256") == source.get("sha256")
                     and source.get("sha256") for source in sources))
    return bool(valid), row.get("dependency_mode") if valid else None


def summary_text(summary, task_data, view, fresh):
    status = "已通过" if summary["outcome"] == "PASSED" and fresh else (
        "未通过" if summary["outcome"] == "FAILED" else "待验收")
    if not fresh:
        status = "待验收"
    task_data = task_data or {}
    lines = [f"# {task_data.get('goal') or (summary.get('task') or {}).get('goal') or '本次验收'}",
             "", f"验收结果：**{status}**", "", "## 改了什么", ""]
    changes = [c for c in task_data.get("changes", []) if c["status"] in {"added", "modified", "removed"}]
    for change in changes:
        lines += [f"- {change['name']}：原来「{change.get('before') or '无已保存说明'}」；"
                  f"现在「{change.get('after') or '无当前说明'}」。"
                  f"原因：{'；'.join(change.get('rationale', [])) or '尚未补充修改原因'}"]
    if not changes:
        lines.append("没有可核对的修改前后说明；本次执行只确认以下测试结果。")
    lines += ["", "## 影响哪里", ""]
    features = [f["name"] for f in task_data.get("impact", {}).get("features", []) if f.get("name")]
    affected = [c for c in task_data.get("changes", []) if c["status"] == "affected"]
    if features:
        lines.append("涉及功能：" + "、".join(dict.fromkeys(features)))
    if affected:
        lines += ["- " + (c.get("after") or c.get("before") or "用途尚未复核") + "（" + c["name"] + "）"
                  for c in affected]
    if not features and not affected:
        lines.append("尚无已登记的关联影响说明。")
    for note in task_data.get("notes", []):
        if note.get("target_type") == "feature" and note.get("kind") == "behavior":
            lines.append(note["text"])
    lines += ["", "## 验收了什么", ""]
    labels = {"passed": "已通过", "failed": "未通过", "blocked": "待验收", "skipped": "待验收"}
    for row in summary["results"]:
        lines.append(f"- {row['title']}：{labels[row['status']]}" + (f"（{row['reason']}）" if row["reason"] else ""))
    lines += ["", "## 还有哪里没验证", ""]
    gaps = list(summary["gaps"])
    gaps += [r["reason"] for r in summary["results"] if r["status"] != "passed" and r["reason"]]
    boundaries = [note["text"] for note in task_data.get("notes", []) if note.get("kind") == "unknown"]
    gaps += boundaries
    if not boundaries:
        gaps.append("尚未登记其他业务或环境的具体验证边界；以上场景之外保持未验证。")
    unresolved = [c for c in task_data.get("changes", []) if c["status"] == "unresolved"]
    if unresolved:
        gaps.append("以下节点无法确认源码变化：" + "、".join(c["name"] for c in unresolved)
                    + "；它们未计作已确认修改，原因见详情页。")
    if not fresh:
        gaps.append("执行期间源码发生变化；需要针对当前版本重新验收。")
    if summary["unselected_ids"]:
        gaps.append("未纳入本次任务的已登记场景：" + "、".join(summary["unselected_ids"]))
    if task_data.get("impact", {}).get("unmapped_changes"):
        gaps.append("分析/讲解缺口（不等同于未执行测试）：以下改动文件尚未映射到功能："
                    + "、".join(c["path"] for c in task_data["impact"]["unmapped_changes"]))
    lines += ["- " + gap for gap in dict.fromkeys(gaps)] or ["已登记范围没有待验收项；未知功能未包含在这个结论中。"]
    deviations = [note["text"] for note in task_data.get("notes", []) if note.get("kind") == "deviation"]
    if deviations:
        lines += ["", "## 与原计划的差异", ""] + ["- " + text for text in deviations]
    lines += ["", f"版本：{summary['run']['revision']}；运行：{summary['run']['id']}", "",
              f"[打开本次任务详情]({view})", ""]
    return "\n".join(lines)


def execute(root, run_dir=None, *, module=None, task=None, assessment=None, resume=False, notes=None):
    root = Path(root).resolve()
    config = root / ".project-check"
    pc.require(config.resolve().is_relative_to(root) and (config / "runs").resolve().is_relative_to(root),
               "Execution configuration/run directory escapes project")
    inventory = pc.validate_inventory(pc.read_json(config / "inventory.json"))
    project = pc.read_json(config / "project.json")
    pc.validate_project(project, inventory)
    scope, selected = pc.select(inventory, module, task)
    pc.require(selected, "No scenarios selected; populate the inventory first")
    pc.require(not resume or run_dir is not None, "Resume requires --run-dir")
    run_dir = Path(run_dir).resolve() if run_dir else config / "runs" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    pc.require(run_dir.is_relative_to((config / "runs").resolve()) and run_dir != (config / "runs").resolve(),
               "Managed execution requires .project-check/runs/<run-id>")
    runners = {r["id"]: r for r in project["runners"] if any(s.get("runner") == r["id"] for s in selected)}
    variables = {"run_dir": run_dir, "run_id": run_dir.name}
    for runner in runners.values():
        pc.require(re.fullmatch(r"[A-Za-z0-9_-]+", runner["id"]), "Unsafe runner id")
        pc.require(not re.fullmatch(r"(setup|cleanup)-[0-9]+", runner["id"]), "Runner id conflicts with lifecycle command")
        pc.require(runner.get("framework") in {"pytest-junit", "playwright-json"},
                   runner["id"] + ": configure framework for managed execution")
        pc.require("{run_dir}" in runner["native_report"], "native_report must use {run_dir} for isolated evidence")
        native = inside(root, expand(runner["native_report"], variables), "native_report")
        pc.require(native.is_relative_to(run_dir), "native_report must stay in the run directory")
        command_spec(root, runner, {**variables, "native_report": native})
    for service in project["services"]:
        pc.require(re.fullmatch(r"[A-Za-z0-9_-]+", service["id"]), "Unsafe service id")
    from project_model import handle
    if not resume:
        handle("map", root)  # Create any missing model before freezing inputs.
        review = pc.read_json(assessment) if assessment else {"version": 1, "scenarios": []}
        pc.require(review.get("version") == 1, "Invalid assessment version")
        pc.records(review.get("scenarios"), "assessment scenarios")
        pc.snapshot(root, run_dir, inventory, {"assessment.json": review})
        state = {"version": 1, "scope": scope, "phase": "running", "started_at": now(), "runners": {}, "commands": {}}
        atomic(run_dir / "execution.json", state)
    with execution_lock(run_dir):
        state = pc.read_json(run_dir / "execution.json")
        pc.require(state.get("scope") == scope, "Resume scope differs from the frozen execution")
        workspace = pc.read_json(run_dir / "workspace.json")
        pc.require(workspace_state.current(workspace, root)["current"], "Source changed since execution snapshot")
        _, problems = pc.verified_manifest("snapshot-manifest.json", run_dir)
        pc.require(not problems, "Execution snapshot changed: " + "; ".join(problems))
        if (run_dir / "results.json").is_file():
            previous = pc.read_json(run_dir / "results.json")
            for manifest in previous["run"]["evidence_manifests"]:
                _, problems = pc.verified_manifest(manifest, run_dir)
                pc.require(not problems, "Frozen execution evidence changed: " + "; ".join(problems))
        review = pc.read_json(run_dir / "assessment.json")
        pc.require(not resume or assessment is None or pc.read_json(assessment) == review,
                   "Resume cannot replace the frozen assertion assessment; start a new run")
        # A missing receipt leaves a command's side effects unknown. Never blindly repeat it.
        for group in ("runners", "commands"):
            for key, row in state.get(group, {}).items():
                pc.require(row.get("phase") != "running" or (run_dir / "commands" / (key + ".json")).is_file(),
                           "Command interrupted with unknown outcome: " + key)
        if state.get("phase") != "complete":
            services = Services(root, run_dir, project)
            setup_error = None
            try:
                for index, item in enumerate(project["data_setup"]):
                    try:
                        for dependency in item.get("requires_services", []):
                            services.ensure(dependency)
                    except ValueError as error:
                        setup_error = str(error)
                        break
                    receipt = run_command(root, run_dir, state, "setup-" + str(index), item, variables)
                    if receipt["error"] or receipt["exit_code"] != 0:
                        setup_error = "Test data setup failed; inspect command logs"
                        break
                readiness = pc.preflight(project, inventory, root, module, task=task)
                gaps = {row["id"]: row["reasons"] for row in readiness["execution_gaps"]}
                for key, runner in runners.items():
                    if state["runners"].get(key, {}).get("imported"):
                        continue
                    native = inside(root, expand(runner["native_report"], variables), "native_report")
                    native.parent.mkdir(parents=True, exist_ok=True)
                    artifacts = inside(root, expand(runner.get("artifact_root", "{run_dir}"), variables), "artifact_root")
                    artifacts.mkdir(parents=True, exist_ok=True)
                    reason = setup_error
                    scoped = [s for s in selected if s.get("runner") == key]
                    if any(s["id"] in gaps for s in scoped):
                        reason = "; ".join(reason for s in scoped for reason in gaps.get(s["id"], []))
                    if not reason:
                        try:
                            for dependency in runner.get("requires_services", []):
                                services.ensure(dependency)
                        except ValueError as error:
                            reason = str(error)
                    receipt = None
                    if not reason:
                        receipt = run_command(root, run_dir, state, key, runner,
                                              {**variables, "native_report": native}, "runners")
                        reason = receipt["error"]
                    try:
                        imported = (import_runner(run_dir, runner, native, artifacts) if not reason and native.is_file()
                                    else blocked_observation(run_dir, key, reason or "Runner produced no native report"))
                    except (OSError, ValueError) as error:
                        # Partial adapter writes must stay visible; never silently repair frozen evidence.
                        if (run_dir / "native").exists() and list((run_dir / "native").glob(key + ".*")):
                            raise ValueError("Evidence import interrupted/invalid; preserve this run and start a new one: " + str(error)) from error
                        imported = blocked_observation(run_dir, key, str(error))
                    state["runners"].setdefault(key, {"phase": "blocked"})["imported"] = imported
                    atomic(run_dir / "execution.json", state)
            finally:
                try:
                    for index, item in enumerate(project["data_cleanup"]):
                        receipt = run_command(root, run_dir, state, "cleanup-" + str(index), item, variables)
                        if receipt["error"] or receipt["exit_code"] != 0:
                            state["cleanup_error"] = "Test data cleanup failed; inspect command logs"
                finally:
                    errors = services.close()
                    if errors:
                        state["cleanup_error"] = "; ".join([state.get("cleanup_error", ""), *errors]).strip("; ")
            state["finished_at"] = state.get("finished_at") or now()
            state["phase"] = "finalizing"
            atomic(run_dir / "execution.json", state)
        return finalize(root, run_dir, inventory, selected, workspace, review, state, module, task, notes)


def finalize(root, run_dir, inventory, selected, workspace, review, state, module, task, notes):
    from project_model import handle
    imports = [row["imported"] for row in state["runners"].values() if row.get("imported")]
    if state.get("cleanup_error"):
        imports.append(blocked_observation(run_dir, "_lifecycle", state["cleanup_error"]))
    if not imports:
        imports.append(blocked_observation(run_dir, "_unimplemented", "No implemented runner for the selected scenarios"))
    rows = []
    for scenario in selected:
        reviewed, mode = reviewed_scenario(review, scenario, workspace)
        row = {"id": scenario["id"], "status": "passed", "reason": "", "assertions_checked": reviewed,
               "dependency_mode": mode, "evidence": [], "interaction_evidence": [], "unexpected_errors": []}
        receipt_file = run_dir / "commands" / (scenario.get("runner", "") + ".json")
        if receipt_file.is_file():
            receipt = pc.read_json(receipt_file)
            if receipt["exit_code"] not in (None, 0):
                row.update(status="failed", reason=f"Test process exited with code {receipt['exit_code']}")
        rows.append(row)
    receipts = [p for folder in ("commands", "services") for p in (run_dir / folder).glob("*")
                if p.is_file() and p.stat().st_size]
    immutable(run_dir / "execution-evidence.json", {"version": 1, "artifacts": [
        {"path": str(p.relative_to(run_dir)), "sha256": pc.sha256(p), "size": p.stat().st_size} for p in sorted(receipts)]})
    payload = {"version": 2, "run": {"id": run_dir.name, "started_at": state["started_at"],
        "finished_at": state["finished_at"], "revision": workspace["revision"], "workspace": workspace["fingerprint"],
        "environment": "Configured local runners; dependency modes require source-bound assessment",
        "scope": state["scope"], "selected_ids": [s["id"] for s in selected],
        "source_review": "source-review.json", "snapshot_manifest": "snapshot-manifest.json",
        "native_reports": [r["native_report"] for r in imports if r.get("native_report")],
        "observations": [r["observations"] for r in imports],
        "evidence_manifests": [r["evidence_manifest"] for r in imports] + ["execution-evidence.json"]}, "results": rows}
    immutable(run_dir / "results.json", payload)
    summary = pc.summarize(inventory, payload, run_dir, module, task)
    fresh = workspace_state.current(workspace, root)["current"]
    immutable(run_dir / "REPORT.md", pc.render_report(summary))
    immutable(run_dir / "report.json", summary)
    if summary["outcome"] == "PASSED" and fresh:
        seal(root, run_dir, module, task)
    if (run_dir / "execution-result.json").is_file():
        result = pc.read_json(run_dir / "execution-result.json")
        pc.require(result["outcome"] == summary["outcome"], "Frozen execution result differs from current evidence")
        return result
    baseline = root / ".project-check/changes" / (task or "") / "baseline.json"
    result = handle("review" if task and baseline.is_file() else "map", root,
                    change_id=task if task and baseline.is_file() else None, run_dir=run_dir, notes=notes)
    view = str(root / ".project-check/views/project.html")
    task_data = result.get("task")
    immutable(run_dir / "SUMMARY.md", summary_text(summary, task_data, view, fresh))
    result = {"outcome": summary["outcome"] if fresh else "INCOMPLETE", "current": fresh, "scope": state["scope"],
            "run_dir": str(run_dir), "summary": str(run_dir / "SUMMARY.md"), "view": view,
            "counts": summary["counts"], "gaps": summary["gaps"], "resumable": True}
    immutable(run_dir / "execution-result.json", result)
    state["phase"] = "complete"
    atomic(run_dir / "execution.json", state)
    return result
