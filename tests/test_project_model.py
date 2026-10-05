import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_model as pm
import workspace_state


class MapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / '.project-check'
        self.config.mkdir()
        self.inventory = {
            'version': 2,
            'modules': [{'id': 'orders', 'name': '订单', 'regression_dependencies': []}],
            'requirement_sources': [{'id': 'spec', 'kind': 'external', 'locator': 'confirmed requirement',
                                     'revision': '1', 'reviewed': True, 'modules': ['orders']}],
            'requirements': [{'id': 'REQ-1', 'module': 'orders', 'title': '保存订单', 'source': 'spec', 'state': 'active'}],
            'scenarios': [{'id': 'SAVE', 'module': 'orders', 'title': '保存后重新读取', 'state': 'active',
                           'requirement_ids': ['REQ-1'], 'kind': 'unit', 'coverage': 'missing',
                           'basis': {'status': 'confirmed', 'source': 'spec'}, 'preconditions': [],
                           'actions': ['保存订单'], 'expected': ['重新读取得到相同内容'], 'tests': [], 'dependency_mode': 'none'}]}
        self.sources = {}
        for path, text in [('page.js', 'export function save() { return api(); }\n'),
                           ('api.py', 'def save():\n    return store()\n'), ('schema.sql', 'CREATE TABLE orders (id integer);\n')]:
            (self.root / path).write_text(text)
            self.sources[path] = {'path': path, 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'line': 1}
        self.model = {
            'version': 1, 'title': '订单项目',
            'features': [{'id': 'order-save', 'name': '保存订单', 'module': 'orders', 'description': '保存订单并重新读取。',
                          'implementation': 'implemented', 'requirement_ids': ['REQ-1'], 'scenario_ids': ['SAVE'],
                          'node_ids': ['page'], 'screen_ids': ['edit']}],
            'nodes': [self.node('page', 'page', 'page.js'), self.node('api', 'api', 'api.py'), self.node('db', 'data', 'schema.sql')],
            'edges': [{'id': 'page-api', 'from': 'page', 'to': 'api', 'kind': 'calls', 'description': '页面发送保存请求。',
                       'provenance': 'inferred', 'sources': []},
                      {'id': 'api-db', 'from': 'api', 'to': 'db', 'kind': 'writes', 'description': '接口写入订单。',
                       'provenance': 'inferred', 'sources': []}],
            'screens': [{'id': 'edit', 'feature': 'order-save', 'title': '订单编辑', 'description': '编辑已有订单。',
                         'provenance': 'observed', 'sources': [self.sources['page.js']], 'fields': [{'label': '订单内容', 'kind': 'input'}],
                         'hotspots': [{'id': 'save', 'label': '保存', 'node_id': 'page', 'description': '提交订单保存。', 'scenario_ids': ['SAVE']}]}]}
        pm.write_json(self.config / 'inventory.json', self.inventory)
        pm.write_json(self.config / 'project.json', {'version': 1, 'name': '订单项目'})
        pm.write_json(self.config / 'model.json', self.model)

    def node(self, key, kind, path):
        return {'id': key, 'kind': kind, 'name': key, 'description': '解释 ' + key, 'provenance': 'observed',
                'sources': [self.sources[path]]}

    def test_discovery_does_not_invent_architecture_or_completion(self):
        discovered = pm.discover(self.root, self.inventory, '订单')
        self.assertEqual(discovered['nodes'], [])
        self.assertEqual(discovered['edges'], [])
        self.assertEqual(discovered['features'][0]['implementation'], 'unknown')
        self.assertEqual(discovered['features'][0]['scenario_ids'], ['SAVE'])
        self.assertTrue(all(row['kind'] == 'unknown' for row in discovered['discovery']['files']))

    def test_map_preserves_reviewed_model(self):
        original = (self.config / 'model.json').read_bytes()
        result = pm.handle('map', self.root)
        self.assertEqual((self.config / 'model.json').read_bytes(), original)
        data = pm.read_json(result['data'])
        self.assertEqual(data['model']['features'][0]['acceptance']['state'], 'incomplete')
        self.assertTrue(Path(result['view']).is_file())

    def test_invalid_reference_fails(self):
        self.model['features'][0]['scenario_ids'] = ['not-a-scenario']
        with self.assertRaisesRegex(ValueError, 'scenario_ids'):
            pm.validate_model(self.model, self.inventory, self.root)

    def test_observed_claim_requires_sources(self):
        self.model['nodes'][0]['sources'] = []
        with self.assertRaisesRegex(ValueError, 'source evidence'):
            pm.validate_model(self.model, self.inventory, self.root)

    def test_line_references_are_verified_against_matching_source(self):
        self.model['nodes'][0]['sources'][0]['line'] = 99
        with self.assertRaisesRegex(ValueError, 'outside file'):
            pm.validate_model(self.model, self.inventory, self.root)

    def test_hotspot_cannot_reference_an_unrelated_scenario(self):
        self.model['screens'][0]['hotspots'][0]['scenario_ids'] = ['INVALID']
        with self.assertRaises(ValueError):
            pm.validate_model(self.model, self.inventory, self.root)

    def test_code_change_marks_evidence_stale_without_rewriting_claim(self):
        (self.root / 'page.js').write_text('changed\n')
        pm.validate_model(self.model, self.inventory, self.root)
        data = pm.view_data(self.root, self.model, self.inventory, pm.acceptance(self.root, self.inventory, None))
        self.assertEqual(data['model']['nodes'][0]['freshness'], 'stale')
        self.assertEqual(data['model']['features'][0]['analysis_state'], 'stale')
        self.assertEqual(self.model['nodes'][0]['sources'][0]['sha256'], self.sources['page.js']['sha256'])

    def test_reverse_impact_reaches_callers_and_features(self):
        value = pm.impact(self.model, self.model, [{'path': 'schema.sql', 'change': 'modified'}])
        self.assertEqual(value['direct_nodes'], ['db'])
        self.assertEqual(value['dependent_nodes'], ['api', 'page'])
        self.assertEqual(value['features'][0]['id'], 'order-save')
        self.assertEqual(value['features'][0]['reason'], 'dependency')

    def test_caller_change_does_not_assert_callee_changed(self):
        value = pm.impact(self.model, self.model, [{'path': 'page.js', 'change': 'modified'}])
        self.assertEqual(value['direct_nodes'], ['page'])
        self.assertEqual(value['dependent_nodes'], [])

    def test_removed_baseline_nodes_still_identify_impact(self):
        after = copy.deepcopy(self.model)
        after['nodes'] = after['nodes'][:2]
        after['edges'] = after['edges'][:1]
        value = pm.impact(self.model, after, [{'path': 'schema.sql', 'change': 'deleted'}])
        self.assertEqual(value['dependent_nodes'], ['api', 'page'])
        self.assertEqual(len(value['features']), 1)

    def test_unmapped_change_is_not_silently_ignored(self):
        value = pm.impact(self.model, self.model, [{'path': 'unknown.py', 'change': 'added'}])
        self.assertEqual(value['unmapped_changes'], [{'path': 'unknown.py', 'change': 'added'}])
        self.assertEqual(value['features'], [])

    def test_baseline_is_immutable_and_ignores_generated_outputs(self):
        pm.handle('baseline', self.root, change_id='edit-order')
        first = pm.handle('review', self.root, change_id='edit-order')
        second = pm.handle('review', self.root, change_id='edit-order')
        self.assertEqual(first['changed_files'], 0)
        self.assertEqual(second['changed_files'], 0)
        self.assertNotEqual(first['history'], second['history'])
        with self.assertRaises(FileExistsError):
            pm.handle('baseline', self.root, change_id='edit-order')

    def test_actual_diff_starts_from_task_baseline_not_git_head(self):
        (self.root / 'api.py').write_text('already_dirty = True\nreturn_value = 1\n')
        pm.handle('baseline', self.root, change_id='save')
        (self.root / 'api.py').write_text('already_dirty = True\nreturn_value = 2\n')
        result = pm.handle('review', self.root, change_id='save')
        review = pm.read_json(result['review'])
        self.assertEqual(review['changes'], [{'path': 'api.py', 'change': 'modified'}])
        diff = review['code_diffs'][0]['diff']
        self.assertIn('-return_value = 1', diff)
        self.assertIn('+return_value = 2', diff)
        self.assertNotIn('+already_dirty', diff)

    def test_credential_and_binary_sources_are_not_copied(self):
        for name, value in [('.env.local', b'TOKEN=some-private-value\n'), ('binary.py', b'\x00binary'),
                            ('secret.pem', b'private'), ('source.py', b'-----BEGIN PRIVATE KEY-----')]:
            (self.root / name).write_bytes(value)
            state = workspace_state.capture(self.root)
            self.assertIn('skipped', pm.source_text(self.root, name, state))

    def test_agent_notes_cannot_self_promote_to_verified(self):
        result = pm.validate_notes({'notes': [{'target_type': 'feature', 'target': 'order-save', 'kind': 'learning',
                                              'text': '解释事务。', 'verified': True, 'provenance': 'observed'}]}, [self.model])
        self.assertFalse(result[0]['verified'])
        self.assertEqual(result[0]['provenance'], 'agent-authored')

    def test_omitted_required_scenario_prevents_feature_pass(self):
        new = copy.deepcopy(self.inventory['scenarios'][0])
        new['id'] = 'SAVE-FAILURE'
        self.inventory['scenarios'].append(new)
        evidence = {'state': 'current', 'outcome': 'PASSED', 'results': [{'id': 'SAVE', 'status': 'passed'}]}
        state = pm.feature_acceptance(self.model['features'][0], self.inventory, evidence)
        self.assertEqual(state['state'], 'incomplete')
        self.assertEqual(state['omitted_scenarios'], ['SAVE-FAILURE'])

    def test_stale_run_cannot_be_presented_as_current_pass(self):
        run = self.config / 'runs' / 'one'
        run.mkdir(parents=True)
        pm.write_json(run / 'inventory.json', self.inventory)
        pm.write_json(run / 'results.json', {'version': 2, 'run': {'scope': 'all', 'snapshot_manifest': 'snapshot-manifest.json'}})
        pm.write_json(run / 'workspace.json', workspace_state.capture(self.root))
        pm.write_json(run / 'snapshot-manifest.json', {'version': 1, 'artifacts': [
            {'path': 'workspace.json', 'sha256': hashlib.sha256((run / 'workspace.json').read_bytes()).hexdigest()}]})
        (self.root / 'api.py').write_text('changed\n')
        summary = {'run': {'id': 'one'}, 'outcome': 'PASSED', 'gaps': [],
                   'results': [{'id': 'SAVE', 'status': 'passed', 'evidence': []}]}
        with patch('project_check.summarize', return_value=summary):
            evidence = pm.acceptance(self.root, self.inventory, run)
        self.assertEqual(evidence['state'], 'stale')
        self.assertEqual(evidence['results'][0]['status'], 'stale')
        self.assertEqual(evidence['results'][0]['historical_status'], 'passed')

    def test_unsealed_or_tampered_workspace_cannot_produce_current_pass(self):
        run = self.config / 'runs' / 'unsealed'
        run.mkdir(parents=True)
        pm.write_json(run / 'inventory.json', self.inventory)
        pm.write_json(run / 'results.json', {'version': 2, 'run': {'scope': 'all', 'snapshot_manifest': 'snapshot-manifest.json'}})
        pm.write_json(run / 'workspace.json', workspace_state.capture(self.root))
        summary = {'run': {'id': 'unsealed'}, 'outcome': 'PASSED', 'gaps': [],
                   'results': [{'id': 'SAVE', 'status': 'passed', 'evidence': []}]}
        for artifacts in [[], [{'path': 'workspace.json', 'sha256': '0' * 64}]]:
            pm.write_json(run / 'snapshot-manifest.json', {'version': 1, 'artifacts': artifacts})
            with patch('project_check.summarize', return_value=summary):
                evidence = pm.acceptance(self.root, self.inventory, run)
            self.assertFalse(evidence['workspace_verified'])
            self.assertEqual(evidence['state'], 'stale')
            self.assertEqual(pm.feature_acceptance(self.model['features'][0], self.inventory, evidence)['state'], 'stale')

    def test_stale_relationship_affects_feature_analysis(self):
        for edge in self.model['edges']:
            edge.update(provenance='observed', sources=[self.sources['api.py']])
        self.model['edges'][0]['sources'] = [{'path': 'api.py', 'sha256': '0' * 64}]
        data = pm.view_data(self.root, self.model, self.inventory, pm.acceptance(self.root, self.inventory, None))
        self.assertEqual(data['model']['features'][0]['analysis_state'], 'stale')

    def test_inferred_relationship_affects_feature_analysis(self):
        for edge in self.model['edges']:
            edge.update(provenance='inferred', sources=[self.sources['api.py']])
        data = pm.view_data(self.root, self.model, self.inventory, pm.acceptance(self.root, self.inventory, None))
        self.assertEqual(data['model']['features'][0]['analysis_state'], 'inferred')

    def test_source_links_reject_traversal_schemes_and_symlinks(self):
        for path in ['../outside', '/tmp/absolute', 'javascript:alert(1)', 'a\\b', 'a\nb']:
            with self.assertRaises(ValueError):
                pm.safe_file(self.root, path)
        outside = Path(self.temporary.name).parent / 'outside-project'
        (self.root / 'link').symlink_to(outside)
        with self.assertRaises(ValueError):
            pm.safe_file(self.root, 'link')

    def test_arbitrary_text_cannot_escape_json_script(self):
        attack = '</script><img src=x onerror=alert(1)><script>alert(2)</script>'
        data = pm.view_data(self.root, self.model, self.inventory, pm.acceptance(self.root, self.inventory, None))
        data['model']['title'] = attack
        html = pm.render_html(data)
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
        parser = Parser()
        parser.feed(html)
        self.assertEqual(parser.tags.count('script'), 2)
        self.assertNotIn('img', parser.tags)
        embedded = html.split('<script type="application/json" id="project-data">', 1)[1].split('</script>', 1)[0]
        self.assertEqual(json.loads(embedded)['model']['title'], attack)
        self.assertNotIn('innerHTML', html)

    def test_source_link_encodes_html_in_filename(self):
        name = '<script>.py'
        (self.root / name).write_text('print(1)')
        link = pm.link_file(self.root, name, self.config / 'views')
        self.assertEqual(link, '../../%3Cscript%3E.py')

    def test_change_id_cannot_escape_project(self):
        with self.assertRaises(ValueError):
            pm.change_path(self.root, '../../outside')

    def data_relationship(self):
        self.model['nodes'][2]['fields'] = [
            {'name': 'id', 'type': 'integer', 'description': '订单的唯一标识', 'primary_key': True},
            {'name': 'customer_id', 'type': 'integer', 'description': '订单所属客户'}]
        customer = copy.deepcopy(self.model['nodes'][2])
        customer.update(id='customer', name='客户', fields=[{'name': 'id', 'type': 'integer', 'description': '客户的唯一标识', 'primary_key': True}])
        self.model['nodes'].append(customer)
        edge = {'id': 'order-customer', 'from': 'db', 'to': 'customer', 'kind': 'references',
                'description': '每张订单属于一位客户，一位客户可以有多张订单。',
                'provenance': 'observed', 'sources': [self.sources['schema.sql']],
                'relationship': {'cardinality': 'many-to-one', 'from_field': 'customer_id', 'to_field': 'id', 'constraint': 'foreign-key'}}
        self.model['edges'].append(edge)
        return edge

    def test_data_fields_and_explicit_foreign_key_are_validated(self):
        self.data_relationship()
        pm.validate_model(self.model, self.inventory, self.root)
        data = pm.view_data(self.root, self.model, self.inventory, pm.acceptance(self.root, self.inventory, None))
        self.assertEqual(data['model']['edges'][-1]['relationship']['cardinality'], 'many-to-one')
        self.assertTrue(data['model']['nodes'][-1]['fields'][0]['primary_key'])

    def test_relationship_rejects_unknown_defined_field(self):
        edge = self.data_relationship()
        edge['relationship']['from_field'] = 'nonexistent_customer'
        with self.assertRaisesRegex(ValueError, 'unknown from_field'):
            pm.validate_model(self.model, self.inventory, self.root)

    def test_inferred_relation_cannot_claim_enforced_foreign_key(self):
        edge = self.data_relationship()
        edge['provenance'] = 'inferred'
        with self.assertRaisesRegex(ValueError, 'enforced foreign-key'):
            pm.validate_model(self.model, self.inventory, self.root)
        edge['relationship']['constraint'] = 'logical'
        pm.validate_model(self.model, self.inventory, self.root)

    def test_relationship_only_connects_data_nodes(self):
        edge = self.data_relationship()
        edge['from'] = 'page'
        with self.assertRaisesRegex(ValueError, 'data endpoints'):
            pm.validate_model(self.model, self.inventory, self.root)

    def test_duplicate_fields_and_nonboolean_primary_keys_fail(self):
        self.data_relationship()
        self.model['nodes'][-1]['fields'].append(copy.deepcopy(self.model['nodes'][-1]['fields'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate field'):
            pm.validate_model(self.model, self.inventory, self.root)
        self.model['nodes'][-1]['fields'].pop()
        self.model['nodes'][-1]['fields'][0]['primary_key'] = 'yes'
        with self.assertRaisesRegex(ValueError, 'primary_key must be boolean'):
            pm.validate_model(self.model, self.inventory, self.root)

    def test_unknown_relationship_can_leave_field_mapping_unresolved(self):
        edge = self.data_relationship()
        edge.update(provenance='unknown', sources=[])
        edge['relationship'] = {'cardinality': 'unknown', 'constraint': 'unknown'}
        pm.validate_model(self.model, self.inventory, self.root)


if __name__ == '__main__':
    unittest.main()
