"""Regressions found during independent review; no browser launch required."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from acceptance import matches, observed_checks
from workspace_state import capture, current


class OperationPrefixTests(unittest.TestCase):
    def test_url_or_selector_cannot_impersonate_action(self):
        for kind in ('click', 'fill', 'check', 'press', 'reload', 'selectOption'):
            for title in (f'Navigate to "http://localhost/{kind}"', f'Navigate to "http://localhost/route.{kind}"',
                          f'Get text locator(".{kind}")'):
                with self.subTest(kind=kind, title=title):
                    check = {'id': 'operation', 'kind': kind, 'passed': True,
                             'operations': [{'title': title, 'category': 'pw:api'}]}
                    self.assertFalse(observed_checks([check])[0]['verified'])

    def test_legacy_and_current_playwright_titles_still_match(self):
        # Titles from Playwright 1.63 coreBundle method metadata, plus old API names.
        examples = {'click': ('locator.click', 'Click getByRole("button")'),
                    'fill': ('locator.fill', 'Fill "new value"'),
                    'check': ('locator.check', 'Check'),
                    'press': ('keyboard.press', 'Press "Enter"'),
                    'goto': ('page.goto', 'Navigate'),
                    'reload': ('page.reload', 'Reload'),
                    'selectOption': ('locator.selectOption', 'Select option')}
        for kind, titles in examples.items():
            for title in titles:
                with self.subTest(kind=kind, title=title):
                    self.assertTrue(matches(kind, {'category': 'pw:api', 'title': title}))


class DirectorySymlinkTests(unittest.TestCase):
    def test_non_git_directory_symlink_retarget_invalidates_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'project'
            root.mkdir()
            targets = [base / 'source-v1', base / 'source-v2']
            for target in targets:
                target.mkdir()
                (target / 'app.py').write_text(target.name)
            link = root / 'src'
            link.symlink_to(targets[0], target_is_directory=True)
            before = capture(root)
            self.assertEqual(before['files']['src']['kind'], 'symlink')
            self.assertNotIn('src/app.py', before['files'])
            link.unlink()
            link.symlink_to(targets[1], target_is_directory=True)
            after = current(before, root)
            self.assertFalse(after['current'])
            self.assertEqual(after['changes'], [{'path': 'src', 'change': 'modified'}])


if __name__ == '__main__':
    unittest.main()
