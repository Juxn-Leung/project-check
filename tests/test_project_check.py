"""Regression tests for report gates; no claim of testing a real application."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "project_check.py"
spec = importlib.util.spec_from_file_location("project_check", SCRIPT)
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


class ReportGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "native.json").write_text('{"fixture": "simulated native result"}')
        (self.base / "actions.txt").write_text("click Create; fill Name; click Save; assert persisted")
        self.inventory = {"version": 1, "modules": [{"id": "customers", "name": "客户管理", "regression_dependencies": []}],
                          "scenarios": [{"id": "CUSTOMERS-001", "module": "customers", "title": "创建并保存", "kind": "e2e",
                                         "state": "active", "basis": {"status": "confirmed", "source": "验收需求"},
                                         "preconditions": ["测试账号"], "actions": ["点击创建", "填写并保存", "刷新"],
                                         "expected": ["记录持久化"], "coverage": "implemented", "runner": "browser",
                                         "tests": ["customers::create"], "dependency_mode": "real"}]}
        self.payload = {"version": 1, "run": {"id": "fixture-run", "started_at": "2026-09-29T10:00:00Z",
                        "finished_at": "2026-09-29T10:01:00Z", "revision": "fixture-revision", "workspace": "clean",
                        "environment": "unit-test fixture, not a real application", "scope": "all",
                        "selected_ids": ["CUSTOMERS-001"], "native_reports": ["native.json"]},
                        "results": [{"id": "CUSTOMERS-001", "status": "passed", "reason": "", "dependency_mode": "real",
                        "observed_tests": [{"id": "customers::create", "status": "passed"}], "assertions_checked": True,
                        "runtime_errors_checked": True, "unexpected_errors": [], "flaky": False,
                        "evidence": ["native.json"], "interaction_evidence": ["actions.txt"]}]}

    def summarize(self, module=None):
        return pc.summarize(self.inventory, self.payload, self.base, module)

    def test_legacy_run_is_not_complete(self):
        self.assertEqual(self.summarize()["outcome"], "INCOMPLETE")

    def test_missing_scenario_result_is_blocked(self):
        self.payload["results"] = []
        report = self.summarize()
        self.assertEqual(report["outcome"], "INCOMPLETE")
        self.assertEqual(report["counts"]["blocked"], 1)

    def test_empty_inventory_never_passes(self):
        self.inventory["scenarios"] = []
        self.payload["results"] = []
        self.payload["run"]["selected_ids"] = []
        self.assertEqual(self.summarize()["outcome"], "INCOMPLETE")

    def test_url_visit_without_interaction_evidence_is_blocked(self):
        self.payload["results"][0]["interaction_evidence"] = []
        self.assertEqual(self.summarize()["counts"]["blocked"], 1)

    def test_unchecked_runtime_errors_are_blocked(self):
        self.payload["results"][0]["runtime_errors_checked"] = False
        self.assertEqual(self.summarize()["counts"]["blocked"], 1)

    def test_runtime_error_overrides_claimed_pass(self):
        self.payload["results"][0]["unexpected_errors"] = ["POST /customers returned 500"]
        self.assertEqual(self.summarize()["outcome"], "FAILED")

    def test_failed_native_subtest_overrides_claimed_pass(self):
        self.payload["results"][0]["observed_tests"][0]["status"] = "failed"
        self.assertEqual(self.summarize()["outcome"], "FAILED")

    def test_retry_success_is_not_clean_pass(self):
        self.payload["results"][0]["flaky"] = True
        self.assertEqual(self.summarize()["outcome"], "FAILED")

    def test_partial_test_discovery_is_blocked(self):
        self.inventory["scenarios"][0]["tests"].append("customers::refresh")
        self.assertEqual(self.summarize()["counts"]["blocked"], 1)

    def test_mocked_backend_cannot_pass_real_e2e(self):
        self.payload["results"][0]["dependency_mode"] = "mocked"
        self.assertEqual(self.summarize()["counts"]["blocked"], 1)

    def test_e2e_inventory_cannot_declare_mocks(self):
        self.inventory["scenarios"][0]["dependency_mode"] = "mocked"
        with self.assertRaises(ValueError):
            self.summarize()

    def test_unconfirmed_basis_leaves_coverage_gap(self):
        self.inventory["scenarios"][0]["basis"]["status"] = "inferred"
        report = self.summarize()
        self.assertEqual(report["counts"]["passed"], 1)
        self.assertEqual(report["outcome"], "INCOMPLETE")

    def test_missing_implementation_cannot_be_claimed_as_pass(self):
        self.inventory["scenarios"][0].update(coverage="missing", tests=[])
        self.assertEqual(self.summarize()["counts"]["blocked"], 1)

    def test_module_with_no_scenarios_is_a_coverage_gap(self):
        self.inventory["modules"].append({"id": "files", "name": "文件", "regression_dependencies": []})
        self.assertEqual(self.summarize()["outcome"], "INCOMPLETE")
        self.payload["run"]["scope"] = "module:customers"
        self.assertEqual(self.summarize("customers")["outcome"], "INCOMPLETE")

    def test_service_cycle_and_unknown_runner_are_rejected(self):
        project = {"version": 1, "name": "fixture", "required_env": [], "data_setup": [], "data_cleanup": [],
                   "services": [], "runners": []}
        with self.assertRaises(ValueError):
            pc.validate_project(project, self.inventory)
        project["services"] = [{"id": "api", "cwd": ".", "start": ["fixture"], "ready_url": "http://localhost/health",
                                "identity_check": "fixture", "timeout_seconds": 10, "depends_on": ["api"]}]
        with self.assertRaisesRegex(ValueError, "cyclic"):
            pc.validate_project(project, self.inventory)

    def test_missing_native_report_blocks_claimed_pass(self):
        (self.base / "native.json").unlink()
        self.assertEqual(self.summarize()["counts"]["blocked"], 1)

    def test_empty_or_external_evidence_is_rejected(self):
        (self.base / "empty.txt").touch()
        outside = self.base.parent / (self.base.name + "-outside.txt")
        outside.write_text("unrelated evidence")
        self.addCleanup(outside.unlink)
        (self.base / "external.txt").symlink_to(outside)
        for value in ("empty.txt", str(outside), "../" + outside.name, "external.txt"):
            with self.subTest(path=value):
                self.assertFalse(pc.evidence_exists(value, self.base))

    def test_duplicate_unknown_and_out_of_scope_results_rejected(self):
        original = copy.deepcopy(self.payload)
        for rows in ([original["results"][0]] * 2, [{"id": "UNKNOWN"}]):
            self.payload["results"] = rows
            with self.assertRaises(ValueError):
                self.summarize()

    def test_skipped_is_incomplete_and_requires_reason(self):
        self.payload["results"][0].update(status="skipped", reason="测试账号未授权")
        self.assertEqual(self.summarize()["outcome"], "INCOMPLETE")
        self.payload["results"][0]["reason"] = ""
        with self.assertRaises(ValueError):
            self.summarize()

    def test_scope_change_is_rejected(self):
        self.payload["run"]["selected_ids"] = []
        with self.assertRaises(ValueError):
            self.summarize()

    def test_module_selection_includes_transitive_dependencies(self):
        self.inventory["modules"] += [{"id": "auth", "name": "登录", "regression_dependencies": ["customers"]},
                                     {"id": "files", "name": "文件", "regression_dependencies": []}]
        self.inventory["modules"][0]["regression_dependencies"] = ["auth"]
        for module in ("auth", "files"):
            row = copy.deepcopy(self.inventory["scenarios"][0])
            row.update(id=module.upper() + "-001", module=module)
            self.inventory["scenarios"].append(row)
        pc.validate_inventory(self.inventory)
        scope, selected = pc.select(self.inventory, "客户管理")
        self.assertEqual(scope, "module:customers")
        self.assertEqual({x["module"] for x in selected}, {"customers", "auth"})
        with self.assertRaises(ValueError):
            pc.select(self.inventory, "nonexistent")

    def test_retired_scenario_requires_reason_and_is_excluded(self):
        self.inventory["scenarios"][0]["state"] = "retired"
        with self.assertRaises(ValueError):
            pc.validate_inventory(self.inventory)
        self.inventory["scenarios"][0]["retirement_reason"] = "需求文档删除了此功能"
        pc.validate_inventory(self.inventory)
        self.assertEqual(pc.select(self.inventory)[1], [])

    def test_duplicate_json_keys_rejected(self):
        path = self.base / "bad.json"
        path.write_text('{"status":"failed","status":"passed"}')
        with self.assertRaises(ValueError):
            pc.read_json(path)

    def test_cli_report_exit_codes_and_no_overwrite(self):
        inventory = self.base / "inventory.json"
        results = self.base / "results.json"
        out = self.base / "REPORT.md"
        inventory.write_text(json.dumps(self.inventory))
        results.write_text(json.dumps(self.payload))
        args = [sys.executable, str(SCRIPT), "report", "--root", str(self.base), "--inventory", str(inventory), "--results", str(results)]
        first = subprocess.run(args + ["--out", str(out)], capture_output=True, text=True)
        self.assertEqual(first.returncode, 1, first.stderr)
        content = out.read_text()
        second = subprocess.run(args + ["--out", str(out)], capture_output=True, text=True)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(out.read_text(), content)
        self.payload["results"][0]["unexpected_errors"] = ["uncaught exception"]
        results.write_text(json.dumps(self.payload))
        failed = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("FAILED", failed.stdout)

    def test_cli_init_is_idempotent_and_does_not_overwrite(self):
        args = [sys.executable, str(SCRIPT), "init", "--root", str(self.base)]
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 0)
        path = self.base / ".project-check" / "project.json"
        original = path.read_text().replace(self.base.name, "user-edited-project")
        path.write_text(original)
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 0)
        self.assertEqual(path.read_text(), original)
        valid = subprocess.run([sys.executable, str(SCRIPT), "validate", "--root", str(self.base)], capture_output=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)


if __name__ == "__main__":
    unittest.main()
