"""Exercise source-bound completion and actual subprocess archive behavior."""
import copy
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(os.environ.get("PROJECT_CHECK_REPO", str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import project_check as pc
import workspace_state
import completion


class CompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / ".project-check/runs/test"
        self.run.mkdir(parents=True)
        self.change = "edit-profile"
        self.active = self.root / "openspec/changes" / self.change
        spec = self.active / "specs/profile/spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("## MODIFIED Requirements\n### Requirement: Save profile\nThe system SHALL save profile.\n#### Scenario: Save and reload\n- WHEN save\n- THEN reload keeps value\n")
        (self.active / "tasks.md").write_text("- [x] Implement and verify profile\n")
        (self.root / "app.py").write_text("def save(): return True\n")
        fake = self.root / "fake_openspec.py"
        fake.write_text('''import json, pathlib, shutil, sys, time
root = pathlib.Path.cwd()
a = sys.argv[1:]
mode = (root / "mode.txt").read_text() if (root / "mode.txt").exists() else "ok"
if a[0] == "status":
 if mode == "preflight-race": (root / "app.py").write_text("changed during preflight")
 print(json.dumps({"changeName": a[2], "isPlanningComplete": mode != "incomplete"}))
elif a[0] == "instructions":
 print(json.dumps({"changeName": a[3], "context": "local test project", "operationGuidance": []}))
elif a[0] == "archive":
 assert a == ["archive", "edit-profile", "--yes"]
 log = root / ".project-check/runs/calls.txt"
 log.write_text((log.read_text() if log.exists() else "") + "archive\\n")
 if mode == "exit-only": sys.exit(0)
 if mode == "timeout-before": time.sleep(2)
 active = root / "openspec/changes" / a[1]
 archived = root / "openspec/changes/archive" / ("2026-10-05-" + a[1])
 archived.parent.mkdir(exist_ok=True)
 shutil.move(str(active), str(archived))
 main = root / "openspec/specs/profile/spec.md"
 if main.exists(): main.write_text("Synced main specification")
 if mode == "source-race": (root / "app.py").write_text("unexpected change")
 if mode == "timeout-after": time.sleep(2)
''')
        self.inventory = {"version": 2, "modules": [{"id": "profile", "name": "Profile", "regression_dependencies": []}],
                          "requirement_sources": [{"id": "delta", "kind": "file", "modules": ["profile"], "reviewed": True,
                                                   "path": str(spec.relative_to(self.root)), "sha256": pc.sha256(spec)}],
                          "requirements": [{"id": "REQ-1", "module": "profile", "title": "Save profile", "source": "delta", "state": "active",
                                            "openspec": {"change": self.change, "spec": "profile", "requirement": "Save profile"}}],
                          "scenarios": [{"id": "PROFILE-1", "module": "profile", "title": "Save and reload", "kind": "unit", "state": "active",
                                         "basis": {"status": "confirmed", "source": "OpenSpec"}, "requirement_ids": ["REQ-1"],
                                         "preconditions": [], "actions": ["save profile"], "expected": ["reload retains data"],
                                         "coverage": "implemented", "runner": "unit", "tests": ["test_profile::test_save"], "dependency_mode": "real",
                                         "openspec": {"change": self.change, "spec": "profile", "requirement": "Save profile", "scenario": "Save and reload"}}]}
        self.project = {"version": 1, "name": "fixture", "services": [], "required_env": [], "data_setup": [], "data_cleanup": [],
                        "runners": [{"id": "unit", "cwd": ".", "argv": ["test"], "discovery": ["list"], "selection": "all", "native_report": "xml", "requires_services": []}],
                        "completion": {"auto_archive": True, "openspec_argv": [sys.executable, str(fake)], "timeout_seconds": 1,
                                       "changes": {self.change: {"requirement_ids": ["REQ-1"], "scenario_ids": ["PROFILE-1"]}}}}
        self.payload = {"version": 2, "run": {"id": "test", "started_at": "2026-10-05T10:00:00Z", "finished_at": "2026-10-05T10:01:00Z",
                        "revision": "fixture", "workspace": "clean", "environment": "test", "scope": "all", "selected_ids": ["PROFILE-1"],
                        "native_reports": ["native.xml"], "source_review": "source-review.json", "snapshot_manifest": "snapshot-manifest.json",
                        "observations": ["obs.json"], "evidence_manifests": ["manifest.json"]},
                        "results": [{"id": "PROFILE-1", "status": "passed", "reason": "", "dependency_mode": "real", "assertions_checked": True,
                                     "unexpected_errors": [], "evidence": [], "interaction_evidence": []}]}
        self.observation = {"version": 1, "cases": [{"id": "test_profile::test_save", "status": "passed", "flaky": False,
                                                 "evidence": ["native.xml"], "interaction_evidence": [], "issues": []}]}
        self.freeze()

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def manifest(self, name, names):
        self.write(self.run / name, {"version": 1, "artifacts": [{"path": p, "sha256": pc.sha256(self.run / p), "size": (self.run / p).stat().st_size} for p in names]})

    def freeze(self):
        self.write(self.root / ".project-check/inventory.json", self.inventory)
        self.write(self.root / ".project-check/project.json", self.project)
        self.write(self.run / "inventory.json", self.inventory)
        self.write(self.run / "project.json", self.project)
        self.write(self.run / "source-review.json", {"version": 1, "sources": pc.source_review(self.inventory, self.root)})
        self.write(self.run / "workspace.json", workspace_state.capture(self.root))
        self.manifest("snapshot-manifest.json", ["inventory.json", "project.json", "source-review.json", "workspace.json"])
        (self.run / "native.xml").write_text('<testsuite><testcase name="save"/></testsuite>')
        self.write(self.run / "obs.json", self.observation)
        self.manifest("manifest.json", ["native.xml", "obs.json"])
        self.write(self.run / "results.json", self.payload)

    def mode(self, value):
        (self.root / "mode.txt").write_text(value)
        self.freeze()

    def seal(self):
        return completion.seal(self.root, self.run)

    def finish(self, execute=False):
        return completion.finish(self.root, self.run, self.change, execute)

    def test_seal_and_dry_run_are_source_bound_and_non_mutating(self):
        sealed = self.seal()
        self.assertEqual(self.seal(), sealed)
        self.assertEqual(self.finish()["outcome"], "READY")
        self.assertTrue(self.active.exists())
        self.assertFalse((self.run.parent / "calls.txt").exists())
        (self.root / "app.py").write_text("changed after seal")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            self.finish(True)

    def test_failed_or_tampered_report_cannot_seal(self):
        self.observation["cases"][0]["status"] = "failed"
        self.freeze()
        with self.assertRaisesRegex(ValueError, "not PASSED"):
            self.seal()
        self.observation["cases"][0]["status"] = "passed"
        self.freeze()
        self.seal()
        (self.run / "native.xml").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "Sealed evidence changed"):
            self.finish(True)

    def test_workspace_must_be_in_frozen_manifest(self):
        self.manifest("snapshot-manifest.json", ["inventory.json", "project.json", "source-review.json"])
        with self.assertRaisesRegex(ValueError, "workspace.json"):
            self.seal()

    def test_output_location_must_be_generated_run(self):
        outside = self.root / "other"
        outside.mkdir()
        with self.assertRaisesRegex(ValueError, "existing run under"):
            completion.seal(self.root, outside)

    def test_archive_policy_is_required(self):
        self.project["completion"]["auto_archive"] = False
        self.freeze()
        self.seal()
        self.assertEqual(self.finish()["outcome"], "READY")
        result = self.finish(True)
        self.assertEqual(result["outcome"], "BLOCKED")
        self.assertIn("not authorized", " ".join(result["gaps"]))
        self.assertTrue(self.active.exists())

    def test_ignored_policy_change_cannot_authorize_old_acceptance(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        (self.root / '.gitignore').write_text('.project-check/\n')
        self.project['completion']['auto_archive'] = False
        self.freeze()
        self.seal()
        self.project['completion']['auto_archive'] = True
        self.write(self.root / '.project-check/project.json', self.project)
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            self.finish(True)
        self.assertTrue(self.active.exists())
        self.assertFalse((self.run.parent / 'calls.txt').exists())

    def test_archive_relocates_exact_sources_and_is_idempotent(self):
        self.seal()
        result = self.finish(True)
        self.assertEqual(result["outcome"], "ARCHIVED")
        self.assertEqual(result["source_bindings"][0]["state"], "relocated")
        self.assertFalse(self.active.exists())
        again = self.finish(True)
        self.assertTrue(again["already_archived"])
        self.assertEqual((self.run.parent / "calls.txt").read_text(), "archive\n")
        self.assertEqual(pc.read_json(self.run / "inventory.json"), self.inventory)

    def test_exit_zero_without_archive_does_not_complete_or_retry(self):
        self.mode("exit-only")
        self.seal()
        self.assertEqual(self.finish(True)["outcome"], "BLOCKED")
        self.assertEqual(self.finish(True)["outcome"], "BLOCKED")
        self.assertEqual((self.run.parent / "calls.txt").read_text(), "archive\n")

    def test_timeout_after_move_is_reconciled_from_exact_archive(self):
        self.mode("timeout-after")
        self.project["completion"]["timeout_seconds"] = .15
        self.freeze()
        self.seal()
        result = self.finish(True)
        self.assertEqual(result["outcome"], "ARCHIVED")
        self.assertEqual(result["archive_command"]["error"], "timeout")

    def test_timeout_before_move_is_blocked(self):
        self.mode("timeout-before")
        self.project["completion"]["timeout_seconds"] = .15
        self.freeze()
        self.seal()
        self.assertEqual(self.finish(True)["outcome"], "BLOCKED")
        self.assertTrue(self.active.exists())

    def test_pending_main_spec_rebind_preserves_historical_evidence(self):
        main = self.root / "openspec/specs/profile/spec.md"
        main.parent.mkdir(parents=True)
        main.write_text("Original main spec")
        self.inventory["requirement_sources"].append({"id": "main", "kind": "file", "modules": ["profile"], "reviewed": True,
                                                       "path": str(main.relative_to(self.root)), "sha256": pc.sha256(main)})
        self.freeze()
        self.seal()
        result = self.finish(True)
        self.assertEqual(result["outcome"], "ARCHIVED_PENDING_REBIND")
        self.assertEqual({x["source_id"]: x["state"] for x in result["source_bindings"]}, {"delta": "relocated", "main": "pending_rebind"})
        self.assertEqual(pc.read_json(self.run / "inventory.json"), self.inventory)

    def test_explicit_human_decision_is_separate_and_seal_bound(self):
        self.project["completion"]["changes"][self.change]["human_approval_required"] = True
        self.freeze()
        self.seal()
        self.assertEqual(self.finish(True)["outcome"], "BLOCKED")
        approval = {"approved": True, "actor": "human", "change": self.change, "seal_sha256": pc.sha256(self.run / "seal.json"),
                    "evidence": "User task message explicitly approved reviewed behavior"}
        self.write(self.run / ("human-approval-" + self.change + ".json"), approval)
        self.assertEqual(self.finish(True)["outcome"], "ARCHIVED")

    def test_unfinished_tasks_and_planning_are_not_acceptance(self):
        (self.active / "tasks.md").write_text("- [ ] Implement profile\n")
        self.freeze()
        self.seal()
        result = self.finish(True)
        self.assertEqual(result["outcome"], "BLOCKED")
        self.assertIn("unfinished", " ".join(result["gaps"]))

    def test_scenario_heading_mapping_cannot_be_inferred(self):
        self.inventory["scenarios"][0].pop("openspec")
        self.freeze()
        self.seal()
        result = self.finish(True)
        self.assertEqual(result["outcome"], "BLOCKED")
        self.assertIn("Unmapped OpenSpec scenario", " ".join(result["gaps"]))

    def test_regression_scope_is_derived_from_inventory(self):
        inventory = copy.deepcopy(self.inventory)
        inventory["modules"].append({"id": "session", "name": "Session", "regression_dependencies": []})
        inventory["modules"][0]["regression_dependencies"] = ["session"]
        extra = copy.deepcopy(inventory["scenarios"][0])
        extra.update(id="SESSION-1", module="session")
        inventory["scenarios"].append(extra)
        mapping = self.project["completion"]["changes"][self.change]
        gaps, _ = completion.mapping_gaps(self.root, self.change, inventory, mapping, ["PROFILE-1"])
        self.assertIn("SESSION-1", " ".join(gaps))

    def test_unrelated_source_change_during_archive_is_not_passed(self):
        self.mode("source-race")
        self.seal()
        result = self.finish(True)
        self.assertEqual(result["outcome"], "BLOCKED")
        self.assertIn("app.py", [x["path"] for x in result["unexpected_source_changes"]])
        self.assertTrue(self.finish(True)["already_archived"])

    def test_completed_archive_recovers_after_interruption_before_receipt(self):
        self.seal()
        result = self.finish(True)
        (self.run / ("archive-" + self.change + ".json")).unlink()
        (self.run / ("archive-" + self.change + ".md")).unlink()
        recovered = self.finish(True)
        self.assertEqual(recovered["outcome"], "ARCHIVED")
        self.assertEqual(recovered["archive_path"], result["archive_path"])
        self.assertEqual((self.run.parent / "calls.txt").read_text(), "archive\n")

    def test_source_change_during_preflight_prevents_mutation(self):
        self.mode("preflight-race")
        self.seal()
        with self.assertRaisesRegex(ValueError, "Source changed during completion preflight"):
            self.finish(True)
        self.assertFalse((self.run.parent / "calls.txt").exists())

    def test_no_tasks_or_numbered_unfinished_tasks_block_archive(self):
        for text in ["No tasks here\n", "1. [ ] Save profile\n"]:
            (self.active / "tasks.md").write_text(text)
            self.freeze()
            for name in ["seal.json", "sealed-report.json", "sealed-report.md"]:
                (self.run / name).unlink(missing_ok=True)
            self.seal()
            self.assertEqual(self.finish(True)["outcome"], "BLOCKED")

    def test_fenced_example_cannot_complete_implementation_tasks(self):
        (self.active / 'tasks.md').write_text('Example only:\n````markdown\n```\n- [x] Implement profile\n```\n````\n')
        self.freeze()
        self.seal()
        result = self.finish(True)
        self.assertEqual(result['outcome'], 'BLOCKED')
        self.assertIn('tasks missing', ' '.join(result['gaps']))
        self.assertFalse((self.run.parent / 'calls.txt').exists())

    def test_fenced_unfinished_example_does_not_block_real_tasks(self):
        (self.active / 'tasks.md').write_text('~~~markdown\n- [ ] Example task\n~~~\n1. [X] Implement profile\n')
        self.freeze()
        self.seal()
        self.assertEqual(self.finish()['outcome'], 'READY')

    def test_commented_tasks_do_not_count_as_implementation(self):
        (self.active / 'tasks.md').write_text('<!--\n- [x] Hidden example\n-->\n')
        self.freeze()
        self.seal()
        self.assertEqual(self.finish(True)['outcome'], 'BLOCKED')
        self.assertFalse((self.run.parent / 'calls.txt').exists())

    def test_commented_examples_do_not_hide_visible_requirements_or_tasks(self):
        (self.active / 'tasks.md').write_text('<!--\n~~~markdown\n- [ ] Hidden example\n-->\n- [x] Implement profile\n')
        spec = self.active / 'specs/profile/spec.md'
        original = spec.read_text()
        spec.write_text('<!--\n```markdown\n' + original + '-->\n' + original)
        parsed = completion.parse_specs(self.root, self.change)
        self.assertEqual(len(parsed['requirements']), 1)
        self.assertEqual(parsed['problems'], [])
        self.inventory['requirement_sources'][0]['sha256'] = pc.sha256(spec)
        self.freeze()
        self.seal()
        self.assertEqual(self.finish()['outcome'], 'READY')

    def test_spec_examples_cannot_supply_delta_headings(self):
        spec = self.active / 'specs/profile/spec.md'
        original = spec.read_text()
        for opening, nested, closing in [('````markdown', '```', '````'),
                                         ('~~~markdown', '```', '~~~'),
                                         ('~~~html <!-- example', '~~~not-a-closing-fence', '~~~'),
                                         ('```markdown', '```not-a-closing-fence', '```')]:
            with self.subTest(opening=opening, nested=nested):
                spec.write_text(opening + '\n' + nested + '\n' + original + closing + '\n')
                parsed = completion.parse_specs(self.root, self.change)
                self.assertEqual(parsed['requirements'], [])
                self.assertIn('No supported requirements', ' '.join(parsed['problems']))
                spec.write_text(opening + '\n' + nested + '\n' + original + closing + '\n' + original)
                parsed = completion.parse_specs(self.root, self.change)
                self.assertEqual(len(parsed['requirements']), 1)
                self.assertEqual(parsed['problems'], [])

    def test_unrecognized_nested_specs_cannot_disappear_from_scope(self):
        extra = self.active / "specs/nested/unsupported/spec.md"
        extra.parent.mkdir(parents=True)
        extra.write_text("## ADDED Requirements\n### Requirement: Hidden scope\n")
        parsed = completion.parse_specs(self.root, self.change)
        self.assertIn("Unsupported spec path", " ".join(parsed["problems"]))

    def test_unsupported_delta_is_explicitly_blocked(self):
        spec = self.active / "specs/profile/spec.md"
        spec.write_text("## RENAMED Requirements\n- FROM: old\n- TO: new\n")
        parsed = completion.parse_specs(self.root, self.change)
        self.assertTrue(parsed["problems"])


if __name__ == "__main__":
    unittest.main()
