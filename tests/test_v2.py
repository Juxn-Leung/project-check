"""Requirement and frozen-evidence gates, plus native report imports."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = load("project_check_v2", ROOT / "scripts/project_check.py")
adapters = load("report_adapters", ROOT / "scripts/report_adapters.py")


class V2GateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "native.xml").write_text("<testsuite><testcase/></testsuite>")
        (self.run / "actions.txt").write_text("click; assert")
        self.inventory = {
            "version": 2,
            "modules": [{"id": "customers", "name": "客户管理", "regression_dependencies": []}],
            "requirement_sources": [{"id": "spec", "kind": "external", "modules": ["customers"],
                                     "locator": "approved brief", "revision": "1", "reviewed": True}],
            "requirements": [{"id": "REQ-1", "module": "customers", "title": "创建客户", "source": "spec", "state": "active"}],
            "scenarios": [{"id": "CUSTOMERS-001", "module": "customers", "title": "创建客户", "kind": "e2e",
                           "state": "active", "basis": {"status": "confirmed", "source": "approved brief"},
                           "requirement_ids": ["REQ-1"], "preconditions": [], "actions": ["点击创建"],
                           "expected": ["记录存在"], "coverage": "implemented", "runner": "browser",
                           "tests": ["suite::create"], "dependency_mode": "real"}],
        }
        self.payload = {"version": 2, "run": {"id": "run-1", "started_at": "2026-09-29T10:00:00Z",
                        "finished_at": "2026-09-29T10:01:00Z", "revision": "fixture", "workspace": "clean",
                        "environment": "fixture", "scope": "all", "selected_ids": ["CUSTOMERS-001"],
                        "native_reports": ["native.xml"], "source_review": "source-review.json",
                        "snapshot_manifest": "snapshot-manifest.json",
                        "observations": ["obs.json"], "evidence_manifests": ["manifest.json"]},
                        "results": [{"id": "CUSTOMERS-001", "status": "passed", "reason": "",
                                     "dependency_mode": "real", "assertions_checked": True,
                                     "unexpected_errors": [], "evidence": [], "interaction_evidence": []}]}
        self.observation = {"version": 1, "cases": [{"id": "suite::create", "status": "passed", "flaky": False,
                            "evidence": ["native.xml"], "interaction_evidence": ["actions.txt"],
                            "runtime_errors_checked": True, "unexpected_errors": [], "issues": []}]}
        self.write_evidence()

    def write_evidence(self):
        (self.run / "inventory.json").write_text(json.dumps(self.inventory))
        (self.run / "project.json").write_text('{"version":1,"name":"fixture"}')
        (self.run / "source-review.json").write_text(json.dumps({"version": 1, "sources": pc.source_review(self.inventory, self.root)}))
        (self.run / "snapshot-manifest.json").write_text(json.dumps({"version": 1, "artifacts": [
            {"path": name, "sha256": pc.sha256(self.run / name), "size": (self.run / name).stat().st_size}
            for name in ("inventory.json", "project.json", "source-review.json")]}))
        (self.run / "obs.json").write_text(json.dumps(self.observation))
        (self.run / "manifest.json").write_text(json.dumps({"version": 1, "artifacts": [
            {"path": name, "sha256": pc.sha256(self.run / name), "size": (self.run / name).stat().st_size}
            for name in ("native.xml", "actions.txt", "obs.json")]}))

    def summarize(self, module=None):
        return pc.summarize(self.inventory, self.payload, self.run, module)

    def test_complete_v2_run_passes_and_lists_requirement(self):
        summary = self.summarize()
        self.assertEqual(summary["outcome"], "PASSED")
        self.assertEqual(summary["modules"][0]["outcome"], "PASSED")
        self.assertEqual(summary["requirements"][0]["scenarios"], ["CUSTOMERS-001"])
        self.assertIn("REQ-1", pc.render_report(summary))
        (self.run / "results.json").write_text(json.dumps(self.payload))
        cli = subprocess.run([sys.executable, str(ROOT / "scripts/project_check.py"), "report", "--root", str(self.root),
                              "--inventory", str(self.run / "inventory.json"), "--results", str(self.run / "results.json")],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn("模块结论", cli.stdout)

    def test_unmapped_requirement_prevents_complete_module(self):
        self.inventory["scenarios"][0]["requirement_ids"] = []
        self.assertEqual(self.summarize()["outcome"], "INCOMPLETE")
        self.assertEqual(self.summarize()["modules"][0]["outcome"], "INCOMPLETE")
        self.assertTrue(any("未映射" in gap for gap in self.summarize()["gaps"]))

    def test_retired_scenario_does_not_map_requirement(self):
        self.inventory["scenarios"][0].update(state="retired", retirement_reason="功能被删除")
        self.payload["run"]["selected_ids"] = []
        self.payload["results"] = []
        self.assertEqual(self.summarize()["outcome"], "INCOMPLETE")
        self.assertTrue(any("未映射" in gap for gap in self.summarize()["gaps"]))

    def test_source_change_requires_new_review_but_does_not_rewrite_old_run(self):
        source = self.root / "requirements.md"
        source.write_text("original")
        self.inventory["requirement_sources"][0] = {"id": "spec", "kind": "file", "modules": ["customers"],
                                                     "path": "requirements.md",
                                                     "sha256": pc.sha256(source), "reviewed": True}
        self.write_evidence()
        self.assertEqual(self.summarize()["outcome"], "PASSED")
        source.write_text("changed")
        self.assertEqual(self.summarize()["outcome"], "PASSED")
        self.write_evidence()
        self.assertEqual(self.summarize()["outcome"], "INCOMPLETE")

    def test_changed_source_with_no_registered_requirement_still_blocks_module(self):
        source = self.root / "additional.md"
        source.write_text("initial")
        self.inventory["requirement_sources"].append({"id": "additional", "kind": "file", "modules": ["customers"],
                                                       "path": "additional.md", "sha256": pc.sha256(source),
                                                       "reviewed": True})
        self.write_evidence()
        self.assertEqual(self.summarize()["outcome"], "PASSED")
        source.write_text("new requirement")
        self.write_evidence()
        self.assertEqual(self.summarize()["modules"][0]["outcome"], "INCOMPLETE")

    def test_module_scope_ignores_unselected_requirement_gap(self):
        self.inventory["modules"].append({"id": "files", "name": "文件", "regression_dependencies": []})
        self.inventory["requirement_sources"][0]["modules"].append("files")
        self.inventory["requirements"].append({"id": "REQ-2", "module": "files", "title": "上传", "source": "spec", "state": "active"})
        self.assertEqual(self.summarize()["outcome"], "INCOMPLETE")
        self.payload["run"]["scope"] = "module:customers"
        self.assertEqual(self.summarize("customers")["outcome"], "PASSED")

    def test_tampered_or_missing_evidence_blocks_pass(self):
        (self.run / "actions.txt").write_text("overwritten")
        self.assertEqual(self.summarize()["results"][0]["status"], "blocked")
        self.write_evidence()
        self.observation["cases"][0]["issues"] = ["missing trace"]
        self.write_evidence()
        self.assertEqual(self.summarize()["results"][0]["status"], "blocked")

    def test_missing_fixture_log_blocks_browser_pass(self):
        self.observation["cases"][0]["runtime_errors_checked"] = False
        self.write_evidence()
        self.assertEqual(self.summarize()["results"][0]["status"], "blocked")

    def test_native_observation_overrides_claimed_green_result(self):
        self.observation["cases"][0]["status"] = "failed"
        self.write_evidence()
        self.assertEqual(self.summarize()["outcome"], "FAILED")
        self.observation["cases"][0]["status"] = "skipped"
        self.write_evidence()
        self.assertEqual(self.summarize()["results"][0]["status"], "blocked")

    def test_manual_native_flags_cannot_replace_adapter(self):
        self.payload["results"][0]["runtime_errors_checked"] = True
        with self.assertRaisesRegex(ValueError, "must come from the adapter"):
            self.summarize()

    def test_snapshot_cli_records_source_review_and_hashes_without_overwrite(self):
        config = self.root / ".project-check"
        config.mkdir()
        (config / "inventory.json").write_text(json.dumps(self.inventory))
        project = {"version": 1, "name": "fixture", "services": [], "required_env": [],
                   "data_setup": [], "data_cleanup": [], "runners": [{"id": "browser", "cwd": ".",
                   "argv": ["fixture"], "discovery": ["fixture"], "selection": "fixture",
                   "native_report": "fixture", "requires_services": []}]}
        (config / "project.json").write_text(json.dumps(project))
        destination = self.root / "fresh-run"
        command = [sys.executable, str(ROOT / "scripts/project_check.py"), "snapshot", "--root", str(self.root),
                   "--run-dir", str(destination)]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(pc.verified_manifest("snapshot-manifest.json", destination)[1], [])
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 2)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.run = self.root / "run"
        self.run.mkdir()

    def test_pytest_junit_statuses_and_parameter_ids(self):
        native = self.root / "junit.xml"
        native.write_text('<testsuite><testcase classname="tests.test_customer" name="test_create[ok]"/>'
                          '<testcase classname="tests.test_customer" name="test_create[bad]"><failure/></testcase>'
                          '<testcase classname="tests.test_customer" name="test_optional"><skipped/></testcase>'
                          '<testcase classname="tests.test_customer" name="test_rerun"><rerunFailure/></testcase></testsuite>')
        result = adapters.adapt_report("pytest-junit", native, self.output, self.run, "unit")
        cases = pc.read_json(self.run / result["observations"])["cases"]
        self.assertEqual([(x["id"], x["status"]) for x in cases], [
            ("tests.test_customer::test_create[ok]", "passed"),
            ("tests.test_customer::test_create[bad]", "failed"),
            ("tests.test_customer::test_optional", "skipped"),
            ("tests.test_customer::test_rerun", "passed")])
        self.assertTrue(cases[-1]["flaky"])
        self.assertEqual(pc.verified_manifest(result["evidence_manifest"], self.run)[1], [])

    def test_playwright_freezes_reused_path_and_browser_log(self):
        trace = self.output / "trace.zip"
        trace.write_bytes(b"old trace")
        native = self.root / "playwright.json"
        event_log = {"version": 1, "checked": True, "unexpected": [], "phases": []}
        native.write_text(json.dumps({"suites": [{"file": "tests/logout.spec.ts", "specs": [{
            "title": "logout", "file": "tests/logout.spec.ts", "tests": [{"projectName": "chromium", "results": [{
                "status": "passed", "retry": 0, "steps": [{"title": "click logout"}],
                "attachments": [{"name": "trace", "path": str(trace), "contentType": "application/zip"},
                                {"name": "project-check-browser-events", "body": json.dumps(event_log),
                                 "contentType": "application/json"}]}]}]}]}]}))
        result = adapters.adapt_report("playwright-json", native, self.output, self.run, "browser")
        case = pc.read_json(self.run / result["observations"])["cases"][0]
        self.assertEqual(case["id"], "chromium::tests/logout.spec.ts::logout")
        self.assertTrue(case["runtime_errors_checked"])
        self.assertGreaterEqual(len(case["interaction_evidence"]), 2)
        frozen = next(path for path in case["interaction_evidence"] if path.endswith("trace.zip"))
        trace.write_bytes(b"new trace")
        self.assertEqual((self.run / frozen).read_bytes(), b"old trace")
        self.assertEqual(pc.verified_manifest(result["evidence_manifest"], self.run)[1], [])

    def test_missing_or_external_attachment_is_reported_without_copying(self):
        native = self.root / "playwright.json"
        native.write_text(json.dumps({"suites": [{"file": "test.spec.ts", "specs": [{"title": "case", "tests": [{
            "results": [{"status": "passed", "attachments": [{"name": "missing", "path": "missing.zip"},
                                                             {"name": "outside", "path": str(native)}]}]}]}]}]}))
        result = adapters.adapt_report("playwright-json", native, self.output, self.run, "browser")
        case = pc.read_json(self.run / result["observations"])["cases"][0]
        self.assertEqual(len(case["issues"]), 2)
        self.assertEqual(result["issues"], 2)

    def test_playwright_retry_is_not_clean_pass(self):
        native = self.root / "playwright.json"
        native.write_text(json.dumps({"suites": [{"file": "test.spec.ts", "specs": [{"title": "case", "tests": [{
            "status": "flaky", "results": [{"status": "failed", "retry": 0, "attachments": []},
                                             {"status": "passed", "retry": 1, "attachments": []}]}]}]}]}))
        result = adapters.adapt_report("playwright-json", native, self.output, self.run, "browser")
        case = pc.read_json(self.run / result["observations"])["cases"][0]
        self.assertEqual(case["status"], "failed")
        self.assertTrue(case["flaky"])

    def test_browser_event_log_alone_is_not_interaction_evidence(self):
        native = self.root / "playwright.json"
        native.write_text(json.dumps({"suites": [{"file": "test.spec.ts", "specs": [{"title": "case", "tests": [{
            "results": [{"status": "passed", "attachments": [{"name": "project-check-browser-events",
                "body": json.dumps({"version": 1, "checked": True, "unexpected": []})}]}]}]}]}]}))
        result = adapters.adapt_report("playwright-json", native, self.output, self.run, "browser")
        case = pc.read_json(self.run / result["observations"])["cases"][0]
        self.assertTrue(case["runtime_errors_checked"])
        self.assertEqual(case["interaction_evidence"], [])


if __name__ == "__main__":
    unittest.main()
