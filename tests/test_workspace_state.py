import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workspace_state import capture, changes, current


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'app.py').write_text('original')

    def test_changed_uncommitted_source_and_new_file_invalidate_snapshot(self):
        before = capture(self.root)
        (self.root / 'app.py').write_text('changed')
        (self.root / 'new.py').write_text('new')
        result = current(before, self.root)
        self.assertFalse(result['current'])
        self.assertEqual({x['path']: x['change'] for x in result['changes']}, {'app.py': 'modified', 'new.py': 'added'})

    def test_generated_outputs_do_not_invalidate_source(self):
        before = capture(self.root)
        for sub in ('runs/run1', 'views', 'changes/task'):
            path = self.root / '.project-check' / sub
            path.mkdir(parents=True)
            (path / 'result.json').write_text('generated')
        self.assertTrue(current(before, self.root)['current'])

    def test_configuration_and_inventory_are_part_of_identity(self):
        before = capture(self.root)
        config = self.root / '.project-check'
        config.mkdir()
        (config / 'inventory.json').write_text('{}')
        self.assertFalse(current(before, self.root)['current'])

    def test_deleted_source_is_reported(self):
        before = capture(self.root)
        (self.root / 'app.py').unlink()
        self.assertEqual(changes(before, capture(self.root)), [{'path': 'app.py', 'change': 'deleted'}])

    def test_symlink_fingerprint_does_not_follow_external_file(self):
        outside = self.root / 'external'
        outside.write_text('secret')
        (self.root / 'link').symlink_to(outside)
        entry = capture(self.root)['files']['link']
        outside.write_text('changed secret')
        self.assertEqual(capture(self.root)['files']['link'], entry)

    def test_git_ignored_artifacts_are_excluded_but_untracked_source_is_included(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        (self.root / '.gitignore').write_text('output.txt\n')
        before = capture(self.root)
        (self.root / 'output.txt').write_text('report')
        self.assertTrue(current(before, self.root)['current'])
        (self.root / 'feature.py').write_text('feature')
        self.assertFalse(current(before, self.root)['current'])

    def test_tracked_code_named_build_is_not_silently_excluded(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        source = self.root / 'src' / 'build'
        source.mkdir(parents=True)
        file = source / 'rules.py'
        file.write_text('old')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        before = capture(self.root)
        file.write_text('new')
        self.assertFalse(current(before, self.root)['current'])

    def test_ignored_acceptance_inputs_are_still_part_of_identity(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        (self.root / '.gitignore').write_text('.project-check/\n')
        config = self.root / '.project-check'
        config.mkdir()
        for name in ('project', 'inventory', 'model'):
            with self.subTest(name=name):
                file = config / (name + '.json')
                file.write_text('{}')
                before = capture(self.root)
                self.assertIn(str(file.relative_to(self.root)), before['files'])
                file.write_text('{"changed": true}')
                self.assertFalse(current(before, self.root)['current'])
                before = capture(self.root)
                file.unlink()
                self.assertFalse(current(before, self.root)['current'])
        before = capture(self.root)
        (config / 'runs').mkdir()
        (config / 'runs/report.json').write_text('{}')
        self.assertTrue(current(before, self.root)['current'])

    def test_tracked_files_behind_directory_symlink_are_never_read(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        source = self.root / 'src'
        source.mkdir()
        (source / 'app.py').write_text('original')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        before = capture(self.root)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name)
        (target / 'app.py').write_text('external content')
        (source / 'app.py').unlink()
        source.rmdir()
        source.symlink_to(target, target_is_directory=True)
        read_bytes = Path.read_bytes

        def guarded_read(file):
            self.assertTrue(file.resolve().is_relative_to(self.root.resolve()), 'followed an external directory symlink')
            return read_bytes(file)

        with patch.object(Path, 'read_bytes', guarded_read):
            after = capture(self.root)
        self.assertEqual(after['files']['src']['kind'], 'symlink')
        self.assertNotIn('src/app.py', after['files'])
        self.assertNotEqual(before['fingerprint'], after['fingerprint'])


if __name__ == '__main__':
    unittest.main()
