import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_check as pc
import project_model as model


class ReadinessTests(unittest.TestCase):
    def test_missing_browser_checks_is_not_execution_ready(self):
        inventory = {'modules': [{'id': 'a'}], 'scenarios': [{'id': 'A', 'module': 'a', 'state': 'active',
                     'runner': 'browser', 'kind': 'browser', 'coverage': 'implemented'}]}
        project = {'required_env': [], 'runners': [{'id': 'browser'}]}
        result = pc.preflight(project, inventory, Path('.'), environment={})
        self.assertFalse(result['ready'])
        self.assertIn('checks', result['execution_gaps'][0]['reasons'][0])

    def test_missing_service_secrets_does_not_block_independent_unit_runner(self):
        inventory = {'modules': [{'id': 'a'}], 'scenarios': [{'id': 'A', 'module': 'a', 'state': 'active',
                     'runner': 'unit', 'kind': 'unit', 'coverage': 'implemented'}]}
        result = pc.preflight({'required_env': ['DATABASE'], 'runners': [{'id': 'unit', 'required_env': []}]},
                              inventory, Path('.'), environment={})
        self.assertTrue(result['ready'])

    def test_verified_module_is_independent_of_another_module_failure(self):
        feature = {'module': 'a', 'scenario_ids': ['A'], 'requirement_ids': ['REQ']}
        inventory = {'scenarios': [{'id': 'A', 'state': 'active', 'requirement_ids': ['REQ']}]}
        evidence = {'state': 'current', 'outcome': 'FAILED', 'module_outcomes': {'a': 'PASSED', 'b': 'FAILED'},
                    'results': [{'id': 'A', 'status': 'passed'}]}
        self.assertEqual(model.feature_acceptance(feature, inventory, evidence)['state'], 'passed')

    def test_missing_historical_run_is_stale_instead_of_green_or_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = model.acceptance(root, {}, root / '.project-check/runs/removed')
            self.assertEqual(result['state'], 'stale')
            self.assertEqual(result['outcome'], 'INCOMPLETE')
