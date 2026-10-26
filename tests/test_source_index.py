import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class SourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'app.py').write_text('''def helper(value: int) -> int:
    """Return a checked value."""
    return value

def save(value):
    return helper(value)

def dynamic(target):
    return target.helper(1)
''')

    def test_python_ranges_contracts_and_unambiguous_call(self):
        from source_index import index_sources
        result = index_sources(self.root)
        nodes = {n['symbol']: n for n in result['nodes']}
        self.assertEqual(set(nodes), {'helper', 'save', 'dynamic'})
        self.assertEqual(nodes['helper']['sources'][0]['end_line'], 3)
        self.assertEqual(nodes['helper']['inputs'][0]['name'], 'value')
        self.assertTrue(any(e['from'] == nodes['save']['id'] and e['to'] == nodes['helper']['id'] for e in result['edges']))
        self.assertTrue(any('target.helper' in x['expression'] for x in result['unresolved_calls']))

    def test_index_does_not_invent_business_purpose_or_coverage(self):
        from source_index import index_sources
        result = index_sources(self.root)
        self.assertTrue(all(n['provenance'] == 'observed' for n in result['nodes']))
        self.assertNotIn('features', result)
        self.assertNotIn('acceptance', result)
        self.assertTrue(all('尚未' in n['description'] or 'Docstring' in n['description'] for n in result['nodes']))

    def test_merge_preserves_agent_semantics_and_is_idempotent(self):
        from source_index import index_sources, merge_index
        result = index_sources(self.root)
        model = {'version': 1, 'title': '项目', 'features': [], 'nodes': [], 'edges': [], 'screens': []}
        merge_index(model, result)
        model['nodes'][0]['description'] = '经阅读后的业务解释'
        merge_index(model, index_sources(self.root))
        self.assertEqual(model['nodes'][0]['description'], '经阅读后的业务解释')
        self.assertEqual(len(model['nodes']), 3)
        # Deleted symbols become a reported gap, not a silent model deletion.
        (self.root / 'app.py').write_text('def helper(value):\n    return value\n')
        merge_index(model, index_sources(self.root))
        self.assertEqual(len(model['nodes']), 3)
        self.assertEqual(len(model['discovery']['missing_symbols']), 2)

    def test_parse_errors_and_unsupported_languages_are_visible(self):
        from source_index import index_sources
        (self.root / 'bad.py').write_text('def broken(:\n')
        (self.root / 'service.go').write_text('package main\n')
        result = index_sources(self.root)
        self.assertEqual(result['parse_errors'][0]['path'], 'bad.py')
        self.assertIn('service.go', result['unsupported_files'])

    def test_symlinks_and_secret_paths_are_not_parsed(self):
        from source_index import index_sources
        (self.root / 'secret.py').write_text('def credential():\n    pass\n')
        (self.root / 'link.py').symlink_to(self.root / 'app.py')
        result = index_sources(self.root)
        self.assertEqual(len(result['nodes']), 3)

    def test_task_paths_and_test_code_are_explicit(self):
        from source_index import index_sources
        (self.root / 'tests').mkdir()
        (self.root / 'tests/test_app.py').write_text('def test_app():\n    pass\n')
        (self.root / 'other.py').write_text('def other():\n    pass\n')
        self.assertEqual(len(index_sources(self.root)['nodes']), 4)
        self.assertEqual(len(index_sources(self.root, include_tests=True)['nodes']), 5)
        self.assertEqual(len(index_sources(self.root, paths=['app.py'])['nodes']), 3)

    def test_same_name_declarations_are_not_collapsed_or_mutated_by_merge(self):
        from source_index import index_sources, merge_index
        (self.root / 'app.py').write_text('def repeated(first):\n    pass\ndef repeated(second):\n    pass\n')
        indexed = index_sources(self.root)
        frozen = json.dumps(indexed, sort_keys=True)
        target = {'version': 1, 'title': '项目', 'features': [], 'nodes': [], 'edges': [], 'screens': []}
        merge_index(target, indexed)
        self.assertEqual(len(target['nodes']), 2)
        self.assertEqual({n['inputs'][0]['name'] for n in target['nodes']}, {'first', 'second'})
        self.assertEqual(json.dumps(indexed, sort_keys=True), frozen)


if __name__ == '__main__':
    unittest.main()
