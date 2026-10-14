"""Task acceptance must be complete without promoting a partial module."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_check as pc
import project_model as model
import completion
import test_v2 as fixtures
import test_completion as completion_fixtures


class TaskScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.V2GateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.inventory = self.fixture.inventory
        self.payload = self.fixture.payload
        self.inventory['tasks'] = [{'id': 'create-customer', 'goal': '创建客户后刷新仍可读取',
                                    'requirement_ids': ['REQ-1'], 'regression_scenario_ids': []}]
        self.payload['run']['scope'] = 'task:create-customer'

    def extra(self, id='UNRELATED', req='OTHER', module='customers'):
        row = copy.deepcopy(self.inventory['scenarios'][0])
        row.update(id=id, module=module, requirement_ids=[req], coverage='missing', tests=[], runner=None)
        row.pop('checks', None)
        self.inventory['scenarios'].append(row)
        self.inventory['requirements'].append({'id': req, 'module': module, 'title': req,
                                              'source': 'spec', 'state': 'active'})
        return row

    def summary(self):
        self.fixture.write_evidence()
        return pc.summarize(self.inventory, self.payload, self.fixture.run, task='create-customer')

    def test_task_ignores_unrelated_gap_but_module_stays_incomplete(self):
        self.extra()
        self.inventory['requirement_sources'].append({'id': 'unreviewed', 'kind': 'external',
            'locator': 'unrelated requirements', 'revision': '1', 'reviewed': False, 'modules': ['customers']})
        self.inventory['requirements'][-1]['source'] = 'unreviewed'
        summary = self.summary()
        self.assertEqual(summary['outcome'], 'PASSED')
        self.assertEqual(summary['modules'][0]['outcome'], 'INCOMPLETE')
        self.assertFalse(summary['modules'][0]['scope_complete'])
        self.assertEqual(summary['unselected_ids'], ['UNRELATED'])
        self.assertIn('任务目标', pc.render_report(summary))

    def test_selection_includes_all_requirement_scenarios_and_transitive_regressions(self):
        self.extra()
        mandatory = copy.deepcopy(self.inventory['scenarios'][0])
        mandatory['id'] = 'SECOND-REQUIRED'
        self.inventory['scenarios'].append(mandatory)
        for id in ['session', 'audit', 'extra']:
            self.inventory['modules'].append({'id': id, 'name': id, 'regression_dependencies': []})
            self.inventory['requirement_sources'][0]['modules'].append(id)
            self.extra(id.upper(), 'REQ-' + id, id)
        self.extra('EXTRA-OTHER', 'REQ-extra-other', 'extra')
        self.inventory['modules'][0]['regression_dependencies'] = ['session']
        self.inventory['modules'][1]['regression_dependencies'] = ['audit']
        self.inventory['tasks'][0]['regression_scenario_ids'] = ['EXTRA']
        pc.validate_inventory(self.inventory)
        scope, scenarios = pc.select(self.inventory, task='create-customer')
        self.assertEqual(scope, 'task:create-customer')
        self.assertEqual({s['id'] for s in scenarios}, {'CUSTOMERS-001', 'SECOND-REQUIRED', 'SESSION', 'AUDIT', 'EXTRA'})

    def test_explicit_regression_does_not_require_other_scenarios_of_its_requirement(self):
        extra = copy.deepcopy(self.inventory['scenarios'][0])
        extra.update(id='REGRESSION', requirement_ids=['REG-REQ'])
        self.inventory['scenarios'].append(extra)
        self.extra('REG-OTHER', 'REG-REQ')
        self.inventory['tasks'][0]['regression_scenario_ids'] = ['REGRESSION']
        self.payload['run']['selected_ids'].append('REGRESSION')
        result = copy.deepcopy(self.payload['results'][0])
        result['id'] = 'REGRESSION'
        self.payload['results'].append(result)
        summary = self.summary()
        self.assertEqual(summary['outcome'], 'PASSED')
        evidence = {'state': 'current', 'scope': 'task:create-customer', 'task': self.inventory['tasks'][0],
                    **{key: summary[key] for key in ['requirements', 'evidence_valid', 'results', 'gaps', 'outcome']}}
        self.assertEqual(model.task_summary(self.inventory, evidence)['status'], 'passed')

    def test_missing_task_requirement_mapping_cannot_pass_with_regression_only(self):
        self.inventory['requirements'].append({'id': 'UNMAPPED', 'module': 'customers', 'title': 'new goal',
                                              'source': 'spec', 'state': 'active'})
        self.inventory['tasks'][0].update(requirement_ids=['UNMAPPED'], regression_scenario_ids=['CUSTOMERS-001'])
        summary = self.summary()
        self.assertEqual(summary['outcome'], 'INCOMPLETE')
        self.assertTrue(any('UNMAPPED' in gap and '未映射' in gap for gap in summary['gaps']))

    def test_required_scenario_cannot_be_omitted_from_payload(self):
        scenario = copy.deepcopy(self.inventory['scenarios'][0])
        scenario['id'] = 'ALSO-REQUIRED'
        self.inventory['scenarios'].append(scenario)
        with self.assertRaisesRegex(ValueError, 'selected_ids mismatch'):
            self.summary()

    def test_zero_scenarios_and_missing_browser_evidence_never_pass(self):
        self.fixture.observation['cases'][0]['checks'] = []
        self.assertEqual(self.summary()['outcome'], 'INCOMPLETE')
        self.inventory['scenarios'] = []
        self.payload['run']['selected_ids'] = []
        self.payload['results'] = []
        self.assertEqual(self.summary()['outcome'], 'INCOMPLETE')

    def test_skipped_or_flaky_task_never_passes(self):
        case = self.fixture.observation['cases'][0]
        case['status'] = 'skipped'
        self.assertEqual(self.summary()['outcome'], 'INCOMPLETE')
        case.update(status='passed', flaky=True)
        self.assertEqual(self.summary()['outcome'], 'FAILED')

    def test_preflight_checks_only_the_selected_task(self):
        self.extra()['required_env'] = ['UNRELATED_SECRET']
        project = {'required_env': [], 'runners': [{'id': 'browser'}]}
        self.assertTrue(pc.preflight(project, self.inventory, self.fixture.root, environment={}, task='create-customer')['ready'])

    def test_full_regression_module_still_requires_all_sources_reviewed(self):
        self.inventory['modules'].append({'id': 'session', 'name': 'session', 'regression_dependencies': []})
        self.inventory['modules'][0]['regression_dependencies'] = ['session']
        self.inventory['requirement_sources'][0]['modules'].append('session')
        self.extra('SESSION', 'SESSION-REQ', 'session')
        self.inventory['requirement_sources'].append({'id': 'session-unread', 'kind': 'external',
            'locator': 'additional session spec', 'revision': '1', 'reviewed': False, 'modules': ['session']})
        self.payload['run']['selected_ids'].append('SESSION')
        summary = self.summary()
        self.assertEqual(summary['outcome'], 'INCOMPLETE')
        self.assertTrue(any('session-unread' in gap for gap in summary['gaps']))

    def test_task_validation_rejects_invalid_references_and_duplicate_ids(self):
        for update in [{'id': '../escape'}, {'goal': ''}, {'requirement_ids': []},
                       {'requirement_ids': ['UNKNOWN']}, {'regression_scenario_ids': ['UNKNOWN']}]:
            with self.subTest(update=update):
                inventory = copy.deepcopy(self.inventory)
                inventory['tasks'][0].update(update)
                with self.assertRaises(ValueError):
                    pc.validate_inventory(inventory)
        self.inventory['tasks'].append(copy.deepcopy(self.inventory['tasks'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate id'):
            pc.validate_inventory(self.inventory)

    def test_unknown_task_and_conflicting_scope_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown task'):
            pc.select(self.inventory, task='unknown')
        with self.assertRaisesRegex(ValueError, 'only one'):
            pc.select(self.inventory, module='customers', task='create-customer')

    def test_task_report_cli_and_scope_mismatch(self):
        self.summary()
        (self.fixture.run / 'results.json').write_text(json.dumps(self.payload))
        command = [sys.executable, str(fixtures.ROOT / 'scripts/project_check.py'), 'report',
                   '--root', str(self.fixture.root), '--inventory', str(self.fixture.run / 'inventory.json'),
                   '--results', str(self.fixture.run / 'results.json'), '--task', 'create-customer']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(command + ['--module', 'customers'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_complete_feature_can_pass_without_promoting_its_module(self):
        self.extra()
        summary = self.summary()
        evidence = {'state': 'current', 'scope': 'task:create-customer', **summary,
                    'module_outcomes': {r['id']: r['outcome'] for r in summary['modules']}}
        feature = {'module': 'customers', 'requirement_ids': ['REQ-1'], 'scenario_ids': ['CUSTOMERS-001']}
        self.assertEqual(model.feature_acceptance(feature, self.inventory, evidence)['state'], 'passed')
        feature.update(requirement_ids=['REQ-1', 'OTHER'], scenario_ids=['CUSTOMERS-001', 'UNRELATED'])
        self.assertEqual(model.feature_acceptance(feature, self.inventory, evidence)['state'], 'incomplete')


class TaskCompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = completion_fixtures.CompletionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.inventory['tasks'] = [{'id': self.fixture.change, 'goal': 'Save profile', 'requirement_ids': ['REQ-1']}]
        self.fixture.payload['run']['scope'] = 'task:' + self.fixture.change

    def test_task_seal_and_archive_ignore_unrelated_module_requirement(self):
        f = self.fixture
        f.inventory['requirements'].append({'id': 'OTHER', 'title': 'Unrelated', 'source': 'delta', 'module': 'profile', 'state': 'active'})
        f.freeze()
        sealed = completion.seal(f.root, f.run, task=f.change)
        self.assertEqual(sealed['scope'], 'task:' + f.change)
        self.assertEqual(f.finish(True)['outcome'], 'ARCHIVED')

    def test_seal_scope_mismatch_and_source_changes_are_rejected(self):
        f = self.fixture
        f.inventory['tasks'].append({'id': 'other', 'goal': 'Other', 'requirement_ids': ['REQ-1']})
        f.freeze()
        with self.assertRaisesRegex(ValueError, 'scope mismatch'):
            completion.seal(f.root, f.run, task='other')
        completion.seal(f.root, f.run, task=f.change)
        evidence = model.acceptance(f.root, f.inventory, f.run)
        self.assertEqual(model.task_summary(f.inventory, evidence)['status'], 'passed')
        (f.root / 'app.py').write_text('changed after acceptance')
        evidence = model.acceptance(f.root, f.inventory, f.run)
        self.assertEqual(model.task_summary(f.inventory, evidence)['status'], 'pending')
        self.assertEqual(evidence['results'][0]['historical_status'], 'passed')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            completion.seal(f.root, f.run, task=f.change)

    def test_partial_task_cannot_archive_a_larger_openspec_change(self):
        f = self.fixture
        spec = f.active / 'specs/profile/spec.md'
        spec.write_text(spec.read_text() + '\n### Requirement: Delete profile\nThe system SHALL delete profile.\n#### Scenario: Delete\n- WHEN delete\n- THEN absent\n')
        f.inventory['requirement_sources'][0]['sha256'] = pc.sha256(spec)
        f.inventory['requirements'].append({'id': 'REQ-2', 'title': 'Delete profile', 'source': 'delta', 'module': 'profile',
            'state': 'active', 'openspec': {'change': f.change, 'spec': 'profile', 'requirement': 'Delete profile'}})
        f.project['completion']['changes'][f.change]['requirement_ids'].append('REQ-2')
        f.freeze()
        completion.seal(f.root, f.run, task=f.change)
        result = f.finish(True)
        self.assertEqual(result['outcome'], 'BLOCKED')
        self.assertTrue(any('omits' in gap for gap in result['gaps']))
        self.assertTrue(f.active.exists())

    def test_review_with_another_task_run_cannot_show_a_pass(self):
        f = self.fixture
        f.freeze()
        evidence = model.acceptance(f.root, f.inventory, f.run)
        f.inventory['tasks'].append({'id': 'other', 'goal': 'Other', 'requirement_ids': ['REQ-1']})
        summary = model.task_summary(f.inventory, evidence, {'change_id': 'other'})
        self.assertEqual(summary['status'], 'pending')
        self.assertTrue(any('另一项任务' in gap for gap in summary['gaps']))

    def test_generated_map_binds_task_summary_and_invalidates_changed_goal(self):
        f = self.fixture
        model.write_json(f.root / '.project-check/model.json', model.discover(f.root, f.inventory, 'Fixture'))
        f.freeze()
        result = model.handle('map', f.root, run_dir=f.run)
        data = pc.read_json(Path(result['data']))
        self.assertEqual(data['task']['id'], f.change)
        self.assertEqual(data['task']['status'], 'passed')
        self.assertEqual(data['acceptance']['source_revision'], 'non-git')
        self.assertIn('"task":', Path(result['view']).read_text())
        f.inventory['tasks'][0]['goal'] = 'Changed goal'
        model.write_json(f.root / '.project-check/inventory.json', f.inventory)
        result = model.handle('map', f.root, run_dir=f.run)
        self.assertEqual(result['task']['status'], 'pending')
        self.assertEqual(result['task']['goal'], 'Changed goal')

    def test_all_run_cannot_promote_unconfirmed_task_basis(self):
        f = self.fixture
        f.payload['run']['scope'] = 'all'
        f.inventory['scenarios'][0]['basis']['status'] = 'inferred'
        model.handle('baseline', f.root, change_id=f.change)
        f.freeze()
        result = model.handle('review', f.root, change_id=f.change, run_dir=f.run)
        data = pc.read_json(Path(result['data']))
        self.assertEqual(data['acceptance']['outcome'], 'INCOMPLETE')
        self.assertEqual(data['task']['status'], 'pending')

    def test_task_feature_cannot_promote_unconfirmed_basis(self):
        f = self.fixture
        f.inventory['scenarios'][0]['basis']['status'] = 'inferred'
        f.freeze()
        evidence = model.acceptance(f.root, f.inventory, f.run)
        feature = {'module': 'profile', 'requirement_ids': ['REQ-1'], 'scenario_ids': ['PROFILE-1']}
        self.assertEqual(evidence['outcome'], 'INCOMPLETE')
        self.assertEqual(model.feature_acceptance(feature, f.inventory, evidence)['state'], 'incomplete')

    def test_all_run_preserves_relevant_runner_failure_and_block(self):
        f = self.fixture
        f.payload['run']['scope'] = 'all'
        for runner_status, expected in [('failed', 'failed'), ('blocked', 'pending')]:
            with self.subTest(runner_status=runner_status):
                f.observation.update(runner_status=runner_status, runner_errors=['teardown could not complete'])
                f.freeze()
                evidence = model.acceptance(f.root, f.inventory, f.run)
                self.assertEqual(model.task_summary(f.inventory, evidence, {'change_id': f.change})['status'], expected)

    def test_unrelated_runner_failure_does_not_fail_task(self):
        f = self.fixture
        f.payload['run']['scope'] = 'all'
        f.freeze()
        evidence = model.acceptance(f.root, f.inventory, f.run)
        evidence['runners'] = [{'id': 'unrelated', 'status': 'failed', 'test_ids': ['other::case'], 'errors': ['unrelated']}]
        self.assertEqual(model.task_summary(f.inventory, evidence, {'change_id': f.change})['status'], 'passed')

    def test_all_run_cannot_hide_empty_regression_module(self):
        f = self.fixture
        f.payload['run']['scope'] = 'all'
        f.inventory['modules'][0]['regression_dependencies'] = ['audit']
        f.inventory['modules'].append({'id': 'audit', 'name': 'Audit', 'regression_dependencies': []})
        f.freeze()
        evidence = model.acceptance(f.root, f.inventory, f.run)
        self.assertEqual(model.task_summary(f.inventory, evidence, {'change_id': f.change})['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
