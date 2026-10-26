"""Run real child processes: journal, freeze and resume without invented acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_check as pc


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        config = self.root / '.project-check'
        config.mkdir()
        (self.root / 'product.py').write_text('def value():\n    return 42\n')
        self.test_source = self.root / 'check.py'
        self.test_source.write_text(
            'import sys\nfrom pathlib import Path\nfrom product import value\n'
            'assert value() == 42\n'
            'p = Path("executions.txt")\np.write_text(p.read_text()+"x" if p.exists() else "x")\n'
            'Path(sys.argv[1]).write_text(\'<testsuite><testcase classname="checks" name="value"/></testsuite>\')\n')
        self.inventory = {'version': 2, 'modules': [{'id': 'm', 'name': '示例', 'regression_dependencies': []}],
            'requirement_sources': [{'id': 'brief', 'kind': 'external', 'modules': ['m'], 'reviewed': True,
                                     'locator': 'value must be 42', 'revision': '1'}],
            'requirements': [{'id': 'R', 'module': 'm', 'source': 'brief', 'title': '返回 42', 'state': 'active'}],
            'scenarios': [{'id': 'S', 'module': 'm', 'title': '读取值', 'kind': 'unit', 'state': 'active',
                'requirement_ids': ['R'], 'basis': {'status': 'confirmed', 'source': 'brief'},
                'coverage': 'implemented', 'preconditions': [], 'actions': ['读取'], 'expected': ['42'],
                'runner': 'unit', 'tests': ['checks::value'], 'dependency_mode': 'none'}],
            'tasks': [{'id': 'value', 'goal': '返回 42', 'requirement_ids': ['R']}]}
        self.project = {'version': 1, 'name': 'Executable fixture', 'services': [], 'required_env': [],
            'data_setup': [], 'data_cleanup': [], 'runners': [{'id': 'unit', 'cwd': '.',
                'argv': [sys.executable, 'check.py', '{native_report}'], 'discovery': [sys.executable, '-V'],
                'selection': 'whole suite', 'native_report': '{run_dir}/raw/unit.xml',
                'framework': 'pytest-junit', 'requires_services': []}]}
        # Test outputs must not invalidate the product snapshot.
        (self.root / '.gitignore').write_text('executions.txt\n')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.save()
        self.assessment = self.root / '.project-check/assessment.json'
        self.write_review()

    def save(self):
        for name, data in [('project', self.project), ('inventory', self.inventory)]:
            (self.root / f'.project-check/{name}.json').write_text(json.dumps(data))

    def write_review(self):
        self.assessment.write_text(json.dumps({'version': 1, 'scenarios': [{'id': 'S',
            'tests': ['checks::value'], 'test_sources': [{'path': 'check.py', 'sha256': pc.sha256(self.test_source)}],
            'assertions': ['assert value() == 42 verifies the approved result'], 'dependency_mode': 'none'}]}))

    def execute(self, name='first', assessment=True, resume=False):
        from execution import execute
        return execute(self.root, self.root / '.project-check/runs' / name, task='value',
                       assessment=self.assessment if assessment else None, resume=resume)

    def test_actual_execution_generates_sealed_evidence_and_task_view(self):
        result = self.execute()
        self.assertEqual(result['outcome'], 'PASSED', result)
        run = self.root / '.project-check/runs/first'
        self.assertTrue((run / 'seal.json').is_file())
        self.assertTrue((run / 'SUMMARY.md').is_file())
        self.assertTrue(Path(result['view']).is_file())
        self.assertEqual((self.root / 'executions.txt').read_text(), 'x')

    def test_resume_is_idempotent_and_revalidates_source(self):
        self.execute()
        again = self.execute(resume=True)
        self.assertEqual(again['outcome'], 'PASSED')
        self.assertEqual((self.root / 'executions.txt').read_text(), 'x')
        (self.root / 'product.py').write_text('def value():\n    return 0\n')
        with self.assertRaisesRegex(ValueError, 'changed|变化'):
            self.execute(resume=True)

    def test_missing_review_runs_tests_but_does_not_claim_business_acceptance(self):
        result = self.execute(assessment=False)
        self.assertEqual(result['outcome'], 'INCOMPLETE')
        self.assertEqual((self.root / 'executions.txt').read_text(), 'x')
        self.assertFalse((self.root / '.project-check/runs/first/seal.json').exists())

    def test_changed_test_cannot_reuse_assertion_review(self):
        self.test_source.write_text(self.test_source.read_text() + '\n# changed\n')
        result = self.execute()
        self.assertEqual(result['outcome'], 'INCOMPLETE')

    def test_nonzero_process_with_green_native_report_is_failed(self):
        self.test_source.write_text(self.test_source.read_text() + '\nsys.exit(3)\n')
        self.write_review()
        self.assertEqual(self.execute()['outcome'], 'FAILED')

    def test_missing_executable_is_blocked_and_reported(self):
        self.project['runners'][0]['argv'] = ['missing-project-check-executable']
        self.save()
        self.assertEqual(self.execute()['outcome'], 'INCOMPLETE')

    def test_unknown_interrupted_command_is_not_reexecuted(self):
        with patch('execution.finalize', side_effect=RuntimeError('interrupted before reporting')):
            with self.assertRaises(RuntimeError):
                self.execute()
        run = self.root / '.project-check/runs/first'
        state = pc.read_json(run / 'execution.json')
        state['runners']['unit'] = {'phase': 'running', 'pid': 99999999}
        state['phase'] = 'running'
        (run / 'execution.json').write_text(json.dumps(state))
        (run / 'commands/unit.json').unlink()
        with self.assertRaisesRegex(ValueError, 'interrupted|中断'):
            self.execute(resume=True)
        self.assertEqual((self.root / 'executions.txt').read_text(), 'x')

    def test_reports_must_be_isolated_from_previous_runs(self):
        self.project['runners'][0]['native_report'] = 'old.xml'
        self.save()
        with self.assertRaisesRegex(ValueError, 'run_dir|运行目录'):
            self.execute()

    def test_resume_after_commands_does_not_repeat_side_effects(self):
        with patch('execution.finalize', side_effect=RuntimeError('agent stopped before reporting')):
            with self.assertRaisesRegex(RuntimeError, 'agent stopped'):
                self.execute()
        self.assertEqual(self.execute(resume=True)['outcome'], 'PASSED')
        self.assertEqual((self.root / 'executions.txt').read_text(), 'x')

    def test_cleanup_failure_is_preserved_in_native_summary_and_view(self):
        self.project['data_cleanup'] = [{'cwd': '.', 'argv': [sys.executable, '-c', 'raise SystemExit(7)']}]
        self.save()
        self.assertEqual(self.execute()['outcome'], 'INCOMPLETE')
        run = self.root / '.project-check/runs/first'
        summary = pc.summarize(pc.read_json(run / 'inventory.json'), pc.read_json(run / 'results.json'), run, task='value')
        self.assertEqual(summary['outcome'], 'INCOMPLETE')
        self.assertTrue(any('cleanup' in gap for gap in summary['gaps']))
        data = pc.read_json(self.root / '.project-check/views/project.json')
        self.assertEqual(data['task']['status'], 'pending')

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group lifecycle')
    def test_cleanup_permission_error_preserves_receipt_and_pending_report(self):
        for name, effects in (
            ('signal-denied', [PermissionError(1, 'Operation not permitted')]),
            ('probe-denied', [None, None, PermissionError(1, 'Operation not permitted')]),
        ):
            with self.subTest(stage=name):
                # The runner really exits; inject the OS failure observed during cleanup.
                with patch('execution.os.killpg', side_effect=effects):
                    result = self.execute(name=name)
                self.assertEqual(result['outcome'], 'INCOMPLETE')
                run = self.root / '.project-check/runs' / name
                receipt = pc.read_json(run / 'commands/unit.json')
                self.assertEqual(receipt['exit_code'], 0)
                self.assertIn('Operation not permitted', receipt['error'])
                self.assertTrue((run / 'SUMMARY.md').is_file())
                self.assertFalse((run / 'seal.json').exists())

    def test_tampering_reports_actual_evidence_problem(self):
        self.execute()
        native = self.root / '.project-check/runs/first/native/unit.xml'
        native.write_text('modified report')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.execute(resume=True)

    def test_timeout_produces_blocked_result_with_diagnostic_receipt(self):
        self.project['runners'][0].update(argv=[sys.executable, '-c', 'import time; time.sleep(20)'],
                                          timeout_seconds=0.1)
        self.save()
        self.assertEqual(self.execute()['outcome'], 'INCOMPLETE')
        receipt = pc.read_json(self.root / '.project-check/runs/first/commands/unit.json')
        self.assertIn('timed out', receipt['error'])

    def test_summary_separates_unresolved_and_preserves_actual_boundaries(self):
        from execution import summary_text
        summary = {'outcome': 'PASSED', 'task': {'goal': '保存'}, 'results': [], 'gaps': [],
                   'unselected_ids': [], 'run': {'id': 'r', 'revision': 'v'}}
        task = {'changes': [{'name': 'mystery', 'status': 'unresolved', 'rationale': []},
                            {'name': 'storage', 'status': 'affected', 'after': '写入客户数据库'}],
                'notes': [{'kind': 'unknown', 'text': '未验证其他浏览器'}],
                'impact': {'features': [{'name': '客户保存'}]}}
        text = summary_text(summary, task, '/tmp/page.html', True)
        self.assertNotIn('mystery', text.split('## 影响哪里')[0])
        self.assertIn('写入客户数据库', text)
        self.assertIn('未验证其他浏览器', text)

    def test_failed_service_does_not_prevent_independent_runner(self):
        import copy
        remote = copy.deepcopy(self.inventory['scenarios'][0])
        remote.update(id='REMOTE', runner='remote', tests=['remote::case'])
        self.inventory['scenarios'].append(remote)
        self.project['services'] = [{'id': 'backend', 'start': ['missing-service-for-project-check'],
            'cwd': '.', 'ready_url': 'http://127.0.0.1:9', 'identity_check': 'expected test service',
            'identity_argv': [sys.executable, '-c', 'pass'], 'timeout_seconds': 1}]
        runner = copy.deepcopy(self.project['runners'][0])
        runner.update(id='remote', requires_services=['backend'], native_report='{run_dir}/raw/remote.xml')
        self.project['runners'].append(runner)
        self.save()
        result = self.execute()
        self.assertEqual(result['outcome'], 'INCOMPLETE')
        self.assertEqual(result['counts']['passed'], 1)
        self.assertEqual(result['counts']['blocked'], 1)

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group lifecycle')
    def test_cleanup_kills_sigterm_ignoring_descendant_after_leader_exits(self):
        from execution import stop
        child = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready",flush=True); time.sleep(30)'
        parent = ('import subprocess,sys,time\n'
                  f'p=subprocess.Popen([sys.executable,"-c",{child!r}],stdout=subprocess.PIPE,text=True)\n'
                  'p.stdout.readline()\nprint(p.pid,flush=True)\ntime.sleep(30)\n')
        process = subprocess.Popen([sys.executable, '-c', parent], stdout=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            child_pid = int(process.stdout.readline())
            error = stop(process)
            self.assertIsNotNone(process.poll())
            try:
                os.kill(child_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            # On systems slow to reap orphan zombies, cleanup must remain unconfirmed.
            self.assertTrue(not alive or error, 'A live descendant was silently treated as cleaned')
        finally:
            stop(process)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
