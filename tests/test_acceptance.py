"""Regression cases where a green runner result is insufficient for acceptance."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from acceptance import missing_checks, observed_checks, validate_checks
from report_adapters import adapt_report


class CheckTests(unittest.TestCase):
    def scenario(self):
        return {'tests': ['test'], 'checks': [
            {'id': 'save', 'kind': 'click', 'description': '保存'},
            {'id': 'refresh', 'kind': 'reload', 'description': '重新加载'},
            {'id': 'persist', 'kind': 'assert', 'description': '重新读取后仍存在'},
        ]}

    def check(self, id, kind, title, category='pw:api', **extra):
        return {'id': id, 'kind': kind, 'passed': True,
                'operations': [{'title': title, 'category': category, **extra}]}

    def test_real_actions_and_readback_assertion_in_order(self):
        scenario = self.scenario()
        validate_checks(scenario['checks'], scenario['tests'])
        observed = observed_checks([self.check('save', 'click', 'locator.click'),
                                    self.check('refresh', 'reload', 'Reload'),
                                    self.check('persist', 'assert', 'expect.toHaveText', 'expect')])
        self.assertEqual(missing_checks(scenario, {'test': {'checks': observed}}), [])

    def test_navigation_is_not_a_click_even_if_wrapper_claims_click(self):
        rows = observed_checks([self.check('save', 'click', 'page.goto')])
        self.assertFalse(rows[0]['verified'])
        self.assertTrue(any('save' in issue for issue in missing_checks(self.scenario(), {'test': {'checks': rows}})))

    def test_empty_step_and_named_fake_expect_do_not_prove_assertion(self):
        for operations in ([], [{'category': 'test.step', 'title': 'expect.toBeVisible'}]):
            row = {'id': 'a', 'kind': 'assert', 'passed': True, 'operations': operations}
            self.assertFalse(observed_checks([row])[0]['verified'])

    def test_failed_or_skipped_child_cannot_be_relabelled_passed(self):
        for extra in ({'error': True}, {'skipped': True}):
            self.assertFalse(observed_checks([self.check('a', 'assert', 'expect', 'expect', **extra)])[0]['verified'])

    def test_readback_before_refresh_is_not_persistence_proof(self):
        rows = [{'id': c['id'], 'kind': c['kind'], 'verified': True} for c in self.scenario()['checks']]
        rows[1], rows[2] = rows[2], rows[1]
        self.assertTrue(any('persist' in issue for issue in missing_checks(self.scenario(), {'test': {'checks': rows}})))

    def test_multiple_tests_must_explicitly_bind_checks(self):
        with self.assertRaisesRegex(ValueError, 'mapped test'):
            validate_checks(self.scenario()['checks'], ['one', 'two'])

    def test_duplicate_observed_checks_cannot_hide_retries(self):
        check = self.check('save', 'click', 'Click')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            observed_checks([check, check])

    def test_legacy_browser_mapping_stays_readable_but_needs_migration(self):
        self.assertTrue(missing_checks({'tests': ['test']}, {}))

    def test_expected_failure_is_not_product_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for final in ('failed', 'passed'):
                run = root / final
                run.mkdir()
                native = root / 'report.json'
                native.write_text(json.dumps({'suites': [{'file': 'test.ts', 'specs': [{'title': 'known bug', 'tests': [{
                    'expectedStatus': 'failed', 'status': 'expected' if final == 'failed' else 'unexpected',
                    'results': [{'status': final, 'attachments': []}]}]}]}]}))
                result = adapt_report('playwright-json', native, root, run, 'browser')
                cases = json.loads((run / result['observations']).read_text())['cases']
                self.assertEqual(cases[0]['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
