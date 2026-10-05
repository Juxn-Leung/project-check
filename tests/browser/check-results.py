"""Verify real browser evidence through the adapter and acceptance summary.

This is an opt-in integration helper, not a simulated native report fixture.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

MODE, REPO, STAGE, OUTPUT = sys.argv[1:]
REPO, STAGE, OUTPUT = map(Path, (REPO, STAGE, OUTPUT))
sys.path.insert(0, str(REPO / 'scripts'))
import project_check as pc
from report_adapters import adapt_report
from workspace_state import current

RUN = OUTPUT / 'run'
CASES = {
    'happy-save-reload': ('PASSED', [('open', 'goto'), ('edit', 'fill'), ('save', 'click'),
                                    ('saved', 'assert'), ('refresh', 'reload'), ('readback', 'assert')]),
    'navigation-only-fake-click': ('INCOMPLETE', [('save', 'click'), ('readback', 'assert')]),
    'known-product-failure': ('FAILED', [('open', 'goto'), ('readback', 'assert')]),
    'empty-assert-wrapper': ('INCOMPLETE', [('open', 'goto'), ('readback', 'assert')]),
    'readback-before-reload': ('INCOMPLETE', [('open', 'goto'), ('refresh', 'reload'), ('readback', 'assert')]),
    **{f'runtime-{fault}': ('FAILED', [('open', 'goto'), ('trigger', 'click'), ('readback', 'assert')])
       for fault in ('console', 'pageerror', 'network', 'requestfailed')},
}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def now():
    return datetime.now(timezone.utc).isoformat()


def case_id(name):
    return f'chromium::acceptance.spec.ts::{name}'


if MODE == 'prepare':
    config = STAGE / '.project-check'
    config.mkdir()
    inventory = {
        'version': 2,
        'modules': [{'id': name, 'name': name, 'regression_dependencies': []} for name in CASES],
        'requirement_sources': [{'id': 'demo-contract', 'kind': 'external', 'modules': list(CASES),
                                 'locator': 'Isolated integration regression expectations, not product requirements',
                                 'revision': '1', 'reviewed': True}],
        'requirements': [{'id': f'REQ-{name}', 'module': name, 'title': name, 'source': 'demo-contract',
                          'state': 'active'} for name in CASES],
        'scenarios': [{
            'id': name, 'module': name, 'title': name, 'kind': 'e2e', 'state': 'active',
            'basis': {'status': 'confirmed', 'source': 'isolated integration regression contract'},
            'requirement_ids': [f'REQ-{name}'], 'preconditions': ['local demo server and disposable JSON data'],
            'actions': [f'{kind}: {key}' for key, kind in checks], 'expected': ['declared actions and assertions actually execute'],
            'coverage': 'implemented', 'runner': 'browser', 'tests': [case_id(name)], 'dependency_mode': 'real',
            'checks': [{'id': key, 'kind': kind, 'description': f'{kind}: {key}'} for key, kind in checks],
        } for name, (_, checks) in CASES.items()],
    }
    project = {'version': 1, 'name': 'Isolated browser integration demonstrator', 'services': [],
               'required_env': [], 'data_setup': [], 'data_cleanup': [], 'runners': [{
                   'id': 'browser', 'cwd': '.', 'argv': ['node', 'playwright/cli.js', 'test'],
                   'discovery': ['node', 'playwright/cli.js', 'test', '--list'],
                   'selection': 'scenario test IDs', 'native_report': 'playwright.json', 'requires_services': [],
               }]}
    write(config / 'inventory.json', inventory)
    write(config / 'project.json', project)
    subprocess.run([sys.executable, str(REPO / 'scripts/project_check.py'), 'snapshot', '--root', str(STAGE),
                    '--run-dir', str(RUN)], check=True)
    write(OUTPUT / 'timing.json', {'started_at': now()})
elif MODE == 'verify':
    imported = adapt_report('playwright-json', OUTPUT / 'playwright.json', OUTPUT / 'artifacts', RUN, 'browser')
    observation = pc.read_json(RUN / imported['observations'])
    observed = {row['id']: row for row in observation['cases']}
    assert set(observed) == {case_id(name) for name in CASES}, f'Unexpected discovered cases: {list(observed)}'
    inventory = pc.read_json(RUN / 'inventory.json')
    workspace = pc.read_json(RUN / 'workspace.json')
    assert current(workspace, STAGE)['current'], 'Source changed during browser verification'
    payload = {
        'version': 2,
        'run': {'id': 'real-browser-integration', 'started_at': pc.read_json(OUTPUT / 'timing.json')['started_at'],
                'finished_at': now(), 'revision': workspace['revision'], 'workspace': workspace['fingerprint'],
                'environment': 'isolated Chromium + local Node HTTP API + disposable JSON file',
                'scope': 'all', 'selected_ids': list(CASES), 'native_reports': [imported['native_report']],
                'source_review': 'source-review.json', 'snapshot_manifest': 'snapshot-manifest.json',
                'observations': [imported['observations']], 'evidence_manifests': [imported['evidence_manifest']]},
        # Deliberately claim every case passed; authoritative native evidence must override the claim.
        'results': [{'id': name, 'status': 'passed', 'reason': '', 'dependency_mode': 'real',
                     'assertions_checked': True, 'unexpected_errors': [], 'evidence': [], 'interaction_evidence': []}
                    for name in CASES],
    }
    write(RUN / 'results.json', payload)
    combined = pc.summarize(inventory, payload, RUN)
    write(OUTPUT / 'acceptance-summary.json', combined)
    results = []
    for name, (expected, _) in CASES.items():
        selected = {**payload, 'run': {**payload['run'], 'scope': f'module:{name}', 'selected_ids': [name]},
                    'results': [row for row in payload['results'] if row['id'] == name]}
        summary = pc.summarize(inventory, selected, RUN, name)
        row = observed[case_id(name)]
        assert summary['outcome'] == expected, f'{name}: expected {expected}, got {summary}'
        assert row['runtime_errors_checked'], f'{name}: auto fixture omitted browser event log'
        assert not row['issues'], f'{name}: attachment import issues {row["issues"]}'
        assert len(row['checks']) == len(CASES[name][1]), f'{name}: native step collection incomplete'
        if name == 'happy-save-reload':
            assert all(check['verified'] for check in row['checks']), row
            assert not row['unexpected_errors'], row
        if name == 'navigation-only-fake-click':
            assert row['status'] == 'passed' and row['checks'][0]['verified'] is False, row
            assert 'save' in summary['results'][0]['reason'], summary
        if name == 'empty-assert-wrapper':
            assert row['status'] == 'passed' and row['checks'][-1]['verified'] is False, row
        if name == 'readback-before-reload':
            assert row['status'] == 'passed' and all(check['verified'] for check in row['checks']), row
            assert 'readback' in summary['results'][0]['reason'], summary
        if name.startswith('runtime-'):
            event_kind = name.removeprefix('runtime-')
            event_kind = 'response' if event_kind == 'network' else event_kind
            assert any(f'unexpected {event_kind}' in error for error in row['unexpected_errors']), row
            assert all(check['verified'] for check in row['checks']), 'Body must pass; teardown must catch runtime fault'
        results.append({'case': name, 'expected': expected, 'actual': summary['outcome'],
                        'native_status': row['status'], 'checks': row['checks'],
                        'unexpected_errors': row['unexpected_errors'], 'reason': summary['results'][0]['reason']})
    native = pc.read_json(OUTPUT / 'playwright.json')
    def native_tests(suites):
        for suite in suites:
            for spec in suite.get('specs', []):
                for test in spec.get('tests', []):
                    yield spec['title'], test
            yield from native_tests(suite.get('suites', []))
    known = next(test for name, test in native_tests(native['suites']) if name == 'known-product-failure')
    assert known['expectedStatus'] == 'failed' and known['status'] == 'expected', known
    write(OUTPUT / 'integration-summary.json', {'passed': len(results), 'scope': 'local demonstrator only', 'cases': results})
    print(f'Browser integration: {len(results)} expected acceptance outcomes verified. Evidence: {OUTPUT}')
elif MODE == 'reporter':
    from acceptance import missing_checks
    RUN.mkdir()
    imported = adapt_report('playwright-json', OUTPUT / 'playwright.json', OUTPUT / 'artifacts', RUN, 'browser')
    cases = pc.read_json(RUN / imported['observations'])['cases']
    rows = {row['id'].rsplit('::', 1)[-1]: row for row in cases}
    assert set(rows) == {'actual-expect', 'empty-wrapper', 'fake-click-wrapper', 'expected-failure', 'reversed-assertions'}, rows
    assert rows['actual-expect']['checks'] == [{'id': 'result', 'kind': 'assert', 'verified': True}], rows['actual-expect']
    assert rows['empty-wrapper']['checks'] == [{'id': 'result', 'kind': 'assert', 'verified': False}], rows['empty-wrapper']
    assert rows['fake-click-wrapper']['checks'] == [{'id': 'save', 'kind': 'click', 'verified': False}], rows['fake-click-wrapper']
    assert rows['expected-failure']['status'] == 'failed', rows['expected-failure']
    assert rows['expected-failure']['checks'][0]['verified'] is False, rows['expected-failure']
    reversed_row = rows['reversed-assertions']
    assert all(check['verified'] for check in reversed_row['checks']), reversed_row
    ordered = {'tests': [reversed_row['id']], 'checks': [
        {'id': key, 'kind': 'assert', 'description': key} for key in ('first', 'second')]}
    missing = missing_checks(ordered, {reversed_row['id']: reversed_row})
    assert len(missing) == 1 and 'second' in missing[0], missing
    for row in rows.values():
        assert row['runtime_errors_checked'] is False, 'No browser ran, so runtime evidence must remain unavailable'
        assert not row['issues'], row
    summary = {'outcome': 'PASSED', 'passed': len(rows), 'scope': 'native reporter contract only; no browser launched',
               'cases': cases}
    write(OUTPUT / 'integration-summary.json', summary)
    print(f'No-browser reporter integration: {len(rows)} contracts verified. Browser acceptance remains unexecuted. Evidence: {OUTPUT}')
else:
    raise SystemExit('Use prepare, verify or reporter')
