"""Method reviews use task snapshots and explicit ranges, not file-level guesses."""
import ast
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_model as pm
import workspace_state


BEFORE = '''def first():
    return "first result"

def second():
    return "second result"

def caller():
    return first()
'''


class MethodChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'methods.py').write_text(BEFORE)
        (self.root / 'schema.sql').write_text('CREATE TABLE orders (id integer PRIMARY KEY);\n')
        self.inventory = {'version': 2, 'modules': [{'id': 'm', 'name': '保存', 'regression_dependencies': []}],
                          'requirements': [], 'requirement_sources': [], 'scenarios': []}
        self.model = {'version': 1, 'title': '方法分析', 'features': [{'id': 'save', 'name': '保存', 'module': 'm',
                       'description': '保存入口', 'implementation': 'unknown', 'node_ids': ['caller', 'first', 'second', 'data'],
                       'entry_node_ids': ['caller'], 'screen_ids': [], 'requirement_ids': [], 'scenario_ids': []}],
                      'nodes': [], 'screens': [], 'edges': []}
        self.refresh(BEFORE)
        self.model['nodes'].append({'id': 'data', 'kind': 'data', 'name': '订单', 'description': '持久化订单',
                                    'provenance': 'observed', 'sources': [self.source('schema.sql', 1, 1)]})
        self.model['edges'] = [self.edge('caller-first', 'caller', 'first'), self.edge('first-data', 'first', 'data', 'writes')]

    def source(self, path, line, end):
        return {'path': path, 'sha256': hashlib.sha256((self.root / path).read_bytes()).hexdigest(), 'line': line, 'end_line': end}

    def edge(self, key, source, target, kind='calls'):
        return {'id': key, 'from': source, 'to': target, 'kind': kind, 'description': '已登记调用关系',
                'provenance': 'inferred', 'sources': []}

    def refresh(self, text):
        (self.root / 'methods.py').write_text(text)
        existing = {n['id']: n for n in self.model['nodes']}
        nodes = []
        for node in ast.parse(text).body:
            if isinstance(node, ast.FunctionDef):
                value = copy.deepcopy(existing.get(node.name, {'id': node.name, 'kind': 'function', 'name': node.name,
                                             'symbol': node.name, 'description': '解释方法用途 ' + node.name,
                                             'provenance': 'observed', 'inputs': [],
                                             'outputs': [{'name': 'result', 'type': 'str', 'description': '计算结果'}],
                                             'steps': ['读取输入', '返回结果'], 'errors': []}))
                value['sources'] = [self.source('methods.py', node.lineno, node.end_lineno)]
                nodes.append(value)
        nodes.extend(n for n in existing.values() if n['kind'] == 'data')
        self.model['nodes'] = nodes

    def baseline(self):
        snapshot = workspace_state.capture(self.root)
        return {'workspace': snapshot, 'model': copy.deepcopy(self.model), 'source_text': pm.capture_text(self.root, self.model, snapshot)}

    def review(self, baseline):
        rows, graph = pm.method_changes(self.root, baseline, self.model, workspace_state.capture(self.root))
        return {r['id']: r for r in rows}, graph

    def test_unrelated_method_in_same_file_is_not_modified(self):
        before = self.baseline()
        self.refresh(BEFORE.replace('first result', 'changed first result'))
        rows, _ = self.review(before)
        self.assertEqual(rows['first']['status'], 'modified')
        self.assertEqual(rows['second']['status'], 'unchanged')
        self.assertEqual(rows['caller']['status'], 'affected')
        self.assertEqual(rows['data']['status'], 'affected')
        self.assertEqual(rows['second']['code_diffs'], [])
        self.assertEqual(rows['data']['code_diffs'], [])
        self.assertIn('不是数据库字段', rows['data']['impact_paths'][0]['reason'])

    def test_unrelated_inserted_lines_shift_range_without_modifying_method(self):
        before = self.baseline()
        self.refresh('# comment inserted before all methods\n\n' + BEFORE)
        rows, _ = self.review(before)
        for key in ('first', 'second', 'caller'):
            self.assertEqual(rows[key]['status'], 'unchanged')
            self.assertEqual(rows[key]['code_diffs'], [])
            self.assertNotEqual(rows[key]['before']['sources'][0]['line'], rows[key]['after']['sources'][0]['line'])

    def test_insert_within_method_marks_only_that_method_modified(self):
        before = self.baseline()
        self.refresh(BEFORE.replace('    return "first result"', '    result = "first result"\n    return result'))
        rows, _ = self.review(before)
        self.assertEqual(rows['first']['status'], 'modified')
        self.assertEqual(rows['second']['status'], 'unchanged')
        self.assertIn('+    result', rows['first']['code_diffs'][0]['diff'])

    def test_removed_method_has_frozen_node_and_old_dependency(self):
        before = self.baseline()
        self.refresh(BEFORE.replace('def first():\n    return "first result"\n\n', ''))
        self.model['edges'] = []
        rows, graph = self.review(before)
        self.assertEqual(rows['first']['status'], 'removed')
        self.assertIsNone(rows['first']['after'])
        self.assertEqual(rows['caller']['status'], 'affected')
        self.assertEqual(rows['first']['before']['code_snippets'][0]['state'], 'frozen')
        self.assertIn('first', {n['id'] for n in graph['nodes']})
        self.assertIn('caller-first', {e['id'] for e in graph['edges']})

    def test_added_method_is_actual_insert_not_model_addition(self):
        before = self.baseline()
        self.refresh(BEFORE + '\ndef newly_added():\n    return "a unique new result"\n')
        rows, _ = self.review(before)
        self.assertEqual(rows['newly_added']['status'], 'added')
        self.assertIsNone(rows['newly_added']['before'])
        self.assertIn('+def newly_added', rows['newly_added']['code_diffs'][0]['diff'])

    def test_model_only_node_addition_does_not_claim_added_code(self):
        before = self.baseline()
        before['model']['nodes'] = [n for n in before['model']['nodes'] if n['id'] != 'second']
        rows, _ = self.review(before)
        self.assertEqual(rows['second']['status'], 'unchanged')
        self.assertTrue(rows['second']['metadata_changed'])

    def test_model_only_removal_does_not_claim_deleted_code(self):
        before = self.baseline()
        self.model['nodes'] = [n for n in self.model['nodes'] if n['id'] != 'second']
        rows, _ = self.review(before)
        self.assertEqual(rows['second']['status'], 'unchanged')
        self.assertTrue(rows['second']['metadata_changed'])
        self.assertIsNone(rows['second']['after'])

    def test_description_change_is_separate_from_actual_source_change(self):
        before = self.baseline()
        self.model['nodes'][0]['description'] = '更准确的方法用途'
        rows, _ = self.review(before)
        self.assertEqual(rows['first']['status'], 'unchanged')
        self.assertTrue(rows['first']['metadata_changed'])
        self.assertEqual(rows['first']['code_diffs'], [])

    def test_missing_saved_source_stays_unresolved(self):
        before = self.baseline()
        before['source_text'].pop('methods.py')
        self.refresh(BEFORE.replace('first result', 'different'))
        rows, _ = self.review(before)
        self.assertEqual(rows['first']['status'], 'unresolved')
        self.assertNotIn('code', rows['first']['before']['code_snippets'][0])

    def test_missing_end_range_does_not_promote_file_edit_to_method_edit(self):
        before = self.baseline()
        before['model']['nodes'][0]['sources'][0].pop('end_line')
        self.refresh(BEFORE.replace('first result', 'different'))
        rows, _ = self.review(before)
        self.assertEqual(rows['first']['status'], 'unresolved')

    def test_stale_source_hash_hides_current_snippet(self):
        before = self.baseline()
        (self.root / 'methods.py').write_text(BEFORE.replace('first result', 'different'))
        rows, _ = self.review(before)
        self.assertEqual(rows['first']['status'], 'unresolved')
        snippet = rows['first']['after']['code_snippets'][0]
        self.assertEqual(snippet['state'], 'stale')
        self.assertNotIn('code', snippet)
        self.assertIn('first result', rows['first']['before']['code_snippets'][0]['code'])

    def test_dirty_task_baseline_is_actual_before(self):
        dirty = BEFORE.replace('first result', 'already dirty')
        self.refresh(dirty)
        before = self.baseline()
        self.refresh(dirty.replace('already dirty', 'task edit'))
        rows, _ = self.review(before)
        diff = rows['first']['code_diffs'][0]['diff']
        self.assertIn('-    return "already dirty"', diff)
        self.assertNotIn('first result', diff)

    def test_old_and_new_callers_are_traced_separately(self):
        before = self.baseline()
        self.refresh(BEFORE.replace('first result', 'new result'))
        self.model['edges'] = [self.edge('caller-first', 'second', 'first')]
        rows, _ = self.review(before)
        self.assertEqual(rows['caller']['status'], 'affected')
        self.assertEqual(rows['second']['status'], 'affected')
        self.assertIn(['first', 'caller'], [p['nodes'] for p in rows['caller']['impact_paths']])
        self.assertIn(['first', 'second'], [p['nodes'] for p in rows['second']['impact_paths']])

    def test_frozen_text_tampering_cannot_create_valid_snippet(self):
        before = self.baseline()
        before['source_text']['methods.py']['text'] = 'fabricated\n'
        rows, _ = self.review(before)
        self.assertEqual(rows['first']['status'], 'unresolved')
        self.assertNotIn('code', rows['first']['before']['code_snippets'][0])

    def test_frozen_credentials_are_not_disclosed(self):
        before = self.baseline()
        credential = '-----BEGIN PRIVATE KEY-----\n'
        sha = hashlib.sha256(credential.encode()).hexdigest()
        before['source_text']['methods.py'] = {'text': credential, 'sha256': sha}
        before['workspace']['files']['methods.py']['sha256'] = sha
        before['model']['nodes'][0]['sources'][0].update(sha256=sha, line=1, end_line=1)
        rows, _ = self.review(before)
        self.assertNotIn('code', rows['first']['before']['code_snippets'][0])
        self.assertIn('credential', rows['first']['before']['code_snippets'][0]['reason'])

    def test_optional_method_contract_and_entry_references_are_validated(self):
        pm.validate_model(self.model, self.inventory, self.root)
        self.model['features'][0]['entry_node_ids'] = ['nonexistent']
        with self.assertRaisesRegex(ValueError, 'entry_node_ids'):
            pm.validate_model(self.model, self.inventory, self.root)
        self.model['features'][0]['entry_node_ids'] = ['caller']
        self.model['nodes'][0]['outputs'][0].pop('description')
        with self.assertRaisesRegex(ValueError, 'outputs requires'):
            pm.validate_model(self.model, self.inventory, self.root)

    def test_review_links_current_source_without_rebinding_frozen_source(self):
        before = self.baseline()
        self.refresh(BEFORE.replace('first result', 'changed result'))
        rows, graph = pm.method_changes(self.root, before, self.model, workspace_state.capture(self.root))
        review = {'node_changes': rows, 'graph_before': graph}
        data = pm.view_data(self.root, self.model, self.inventory, pm.acceptance(self.root, self.inventory, None), review)
        result = next(row for row in data['review']['node_changes'] if row['id'] == 'first')
        self.assertEqual(result['after']['sources'][0]['href'], '../../methods.py')
        self.assertNotIn('href', result['before']['sources'][0])
        self.assertNotIn('href', review['node_changes'][0]['after']['sources'][0])


if __name__ == '__main__':
    unittest.main()
