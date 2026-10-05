"""Match scenario checks to observed Playwright API operations and assertions."""
import re

KINDS = {'click', 'fill', 'selectOption', 'check', 'press', 'goto', 'reload', 'assert'}
ALIASES = {'goto': 'navigate', 'selectOption': 'select option', 'assert': 'expect'}


def validate_checks(checks, tests):
    if not isinstance(checks, list) or not checks:
        raise ValueError('checks must be a nonempty ordered list')
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or not re.fullmatch(r'[A-Za-z0-9_-]+', str(check.get('id', ''))):
            raise ValueError('check needs a stable id using letters, digits, _ or -')
        if check['id'] in seen or check.get('kind') not in KINDS or not str(check.get('description', '')).strip():
            raise ValueError('duplicate check id, invalid kind or missing description')
        seen.add(check['id'])
        if check.get('test', tests[0] if len(tests) == 1 else None) not in tests:
            raise ValueError('each check must identify its mapped test when multiple tests are used')
    if not any(check['kind'] == 'assert' for check in checks):
        raise ValueError('checks must include a business assertion')


def matches(kind, operation):
    if operation.get('error') or operation.get('skipped'):
        return False
    if kind == 'assert':
        return operation.get('category') == 'expect'
    if operation.get('category') != 'pw:api':
        return False
    title = str(operation.get('title', '')).lower()
    # Match the operation prefix, never action words embedded in a URL or selector.
    return bool(re.match(r'(?:[a-z_]\w*\.)*' + re.escape(kind.lower()) + r'(?:\b|\()', title) or
                (kind in ALIASES and re.match(re.escape(ALIASES[kind]) + r'\b', title)))


def observed_checks(checks):
    result = []
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or check.get('kind') not in KINDS or not isinstance(check.get('operations'), list):
            raise ValueError('invalid observed check')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', str(check.get('id', ''))):
            raise ValueError('invalid observed check id')
        if check['id'] in seen:
            raise ValueError('duplicate observed check id; retries cannot replace a failed check')
        seen.add(check['id'])
        if not all(isinstance(op, dict) for op in check['operations']):
            raise ValueError('invalid observed operation')
        verified = (check.get('passed') is True and any(matches(check['kind'], op) for op in check['operations'])
                    and not any(op.get('error') or op.get('skipped') for op in check['operations']))
        result.append({'id': check['id'], 'kind': check['kind'], 'verified': verified})
    return result


def missing_checks(scenario, observed):
    checks = scenario.get('checks')
    if not checks:
        return ['没有登记可核对的操作和业务断言；旧清单需补充 checks']
    missing = []
    cursors = {}
    for check in checks:
        test = check.get('test', scenario['tests'][0])
        rows = observed.get(test, {}).get('checks', [])
        start = cursors.get(test, 0)
        match = next((i for i in range(start, len(rows)) if rows[i].get('id') == check['id']
                      and rows[i].get('kind') == check['kind'] and rows[i].get('verified') is True), None)
        if match is None:
            missing.append(f"未观察到按顺序完成的 {check['kind']}：{check['id']}（{check['description']}）")
        else:
            cursors[test] = match + 1
    return missing
