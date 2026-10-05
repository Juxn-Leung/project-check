"""Evidence-linked offline project maps. Semantic descriptions are agent-authored.

Observed file identities/diffs never substitute for browser acceptance evidence.
"""
from collections import deque
from datetime import datetime, timezone
import copy
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import quote

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import workspace_state

NODE_KINDS = {'page', 'component', 'api', 'function', 'data', 'external'}
EDGE_KINDS = {'calls', 'reads', 'writes', 'navigates', 'contains', 'depends_on', 'references'}
PROVENANCE = {'observed', 'inferred', 'unknown'}
SOURCE_LIMIT = 200_000
BASELINE_TEXT_LIMIT = 1_000_000
SNIPPET_LINES = 100
SNIPPET_CHARS = 16_000


def text_safety(path, content):
    """Apply the same bounded disclosure rules to current and frozen contents."""
    parts = PurePosixPath(path).parts
    if any(re.search(r'(^\.env($|\.)|(^|[._-])(secret|secrets|credential|credentials|id_rsa|id_ed25519)([._-]|$)|\.pem$|\.key$|\.p12$|^\.npmrc$)', p, re.I) for p in parts):
        return 'credential-like path'
    if len(content) > SOURCE_LIMIT:
        return 'source exceeds 200 KB text limit'
    if b'\0' in content:
        return 'binary source'
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        return 'source is not UTF-8'
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b', text):
        return 'credential-like source contents'
    return None


def source_text(root, path, snapshot):
    """Only explicit model sources, with bounded text and credential exclusions."""
    reason = text_safety(path, b'')
    if reason:
        return {'skipped': reason}
    file = safe_file(root, path)
    if not file.is_file() or file.is_symlink():
        return {'skipped': 'not a regular source file'}
    if file.stat().st_size > SOURCE_LIMIT:
        return {'skipped': 'source exceeds 200 KB text limit'}
    content = file.read_bytes()
    reason = text_safety(path, content)
    if reason:
        return {'skipped': reason}
    sha = hashlib.sha256(content).hexdigest()
    if snapshot['files'].get(path, {}).get('sha256') != sha:
        return {'skipped': 'source changed during capture'}
    return {'text': content.decode('utf-8'), 'sha256': sha}


def capture_text(root, model, snapshot):
    result, size = {}, 0
    for path in sorted(mapped_paths(model)):
        source = source_text(root, path, snapshot)
        if 'text' in source:
            size += len(source['text'].encode('utf-8'))
            if size > BASELINE_TEXT_LIMIT:
                source = {'skipped': 'baseline exceeds 1 MB text limit'}
        result[path] = source
    return result


def code_diffs(root, baseline, model, after, changes):
    result = []
    paths = mapped_paths(baseline['model']) | mapped_paths(model)
    for change in changes:
        path = change['path']
        if path not in paths:
            continue
        old = checked_record(path, baseline.get('source_text', {}).get(path, {}), baseline['workspace'])
        old_exists = baseline['workspace']['files'].get(path, {}).get('kind') not in {None, 'deleted'}
        if not old_exists:
            old = {'text': '', 'sha256': None}
        new_exists = after['files'].get(path, {}).get('kind') not in {None, 'deleted'}
        new = source_text(root, path, after) if new_exists else {'text': '', 'sha256': None}
        if 'text' not in old or 'text' not in new:
            result.append({'path': path, 'available': False,
                           'reason': old.get('skipped') or new.get('skipped') or 'source was not mapped when baseline was captured'})
            continue
        lines = list(difflib.unified_diff(old['text'].splitlines(), new['text'].splitlines(),
                                         fromfile='before/' + path, tofile='after/' + path, lineterm=''))
        text = '\n'.join(lines[:600])
        result.append({'path': path, 'available': True, 'before_sha256': old['sha256'], 'after_sha256': new['sha256'],
                       'diff': text[:64000], 'truncated': len(lines) > 600 or len(text) > 64000})
    return result


def checked_record(path, record, snapshot):
    """Frozen text is untrusted until its content identity matches the snapshot."""
    if not isinstance(record, dict) or not isinstance(record.get('text'), str):
        return {'skipped': record.get('skipped', 'source text was not saved') if isinstance(record, dict) else 'source text was not saved'}
    content = record['text'].encode('utf-8')
    reason = text_safety(path, content)
    if reason:
        return {'skipped': reason}
    sha = hashlib.sha256(content).hexdigest()
    if record.get('sha256') != sha or snapshot['files'].get(path, {}).get('sha256') != sha:
        return {'skipped': 'source text identity does not match its snapshot'}
    return {'text': record['text'], 'sha256': sha}


def mapped_range(source, record, snapshot):
    """Return only an explicitly bounded, fresh range, never a guessed method."""
    if source.get('sha256') != snapshot['files'].get(source['path'], {}).get('sha256'):
        return {'state': 'stale', 'reason': '登记的来源哈希与该时点源码不一致。'}
    record = checked_record(source['path'], record, snapshot)
    if 'text' not in record:
        return {'state': 'unavailable', 'reason': record['skipped']}
    if type(source.get('line')) is not int or type(source.get('end_line')) is not int:
        return {'state': 'unavailable', 'reason': '缺少明确的 line..end_line，无法定位整个方法。'}
    start, end = source['line'] - 1, source['end_line']
    lines = record['text'].splitlines(keepends=True)
    if not 0 <= start < end <= len(lines):
        return {'state': 'unavailable', 'reason': '登记区间超出该时点源码范围。'}
    return {'state': 'available', 'start': start, 'end': end, 'lines': lines, 'text': ''.join(lines[start:end])}


def node_snapshot(node, snapshot, records, frozen=False):
    if node is None:
        return None
    value = copy.deepcopy(node)
    freshness = source_state(node.get('sources', []), snapshot)
    value['freshness'] = 'frozen' if frozen and freshness == 'current' else freshness
    value['code_snippets'] = []
    for source in node.get('sources', []):
        part = mapped_range(source, records.get(source['path'], {}), snapshot)
        snippet = {'path': source['path'], 'line': source.get('line'), 'end_line': source.get('end_line'),
                   'state': part['state'], 'truncated': False}
        if part['state'] == 'available':
            lines = part['text'].splitlines(keepends=True)
            text = ''.join(lines[:SNIPPET_LINES])
            snippet.update(state='frozen' if frozen else 'current', code=text[:SNIPPET_CHARS],
                           truncated=len(lines) > SNIPPET_LINES or len(text) > SNIPPET_CHARS)
        else:
            snippet['reason'] = part['reason']
        value['code_snippets'].append(snippet)
    return value


def range_diff(path, old, new):
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                     fromfile='before/' + path, tofile='after/' + path, lineterm=''))
    text = '\n'.join(lines[:600])
    return {'path': path, 'available': True, 'diff': text[:64000], 'truncated': len(lines) > 600 or len(text) > 64000}


def overlap(start, end, left, right):
    return start < right and left < end


def metadata(node):
    # Hashes and line shifts identify evidence, not semantic documentation changes.
    return {key: value for key, value in (node or {}).items() if key not in {'sources', 'freshness', 'code_snippets'}}


def method_changes(root, baseline, model, after):
    """Classify bounded source ranges; absence from model never proves deletion.

    A line diff is evidence about mapped text, not a language-wide semantic parser.
    Unbounded, stale or unavailable method ranges remain unresolved.
    """
    old_model = baseline['model']
    old_nodes = {node['id']: node for node in old_model['nodes']}
    new_nodes = {node['id']: node for node in model['nodes']}
    old_records = baseline.get('source_text', {})
    new_records = {path: source_text(root, path, after) for path in mapped_paths(old_model) | mapped_paths(model)}
    file_ops = {}

    def operations(path):
        if path not in file_ops:
            old = checked_record(path, old_records.get(path, {}), baseline['workspace'])
            new = checked_record(path, new_records.get(path, {}), after)
            old_exists = baseline['workspace']['files'].get(path, {}).get('kind') not in {None, 'deleted'}
            new_exists = after['files'].get(path, {}).get('kind') not in {None, 'deleted'}
            if not old_exists:
                old = {'text': ''}
            if not new_exists:
                new = {'text': ''}
            if 'text' not in old or 'text' not in new:
                file_ops[path] = None
            else:
                file_ops[path] = difflib.SequenceMatcher(None, old['text'].splitlines(keepends=True),
                                                        new['text'].splitlines(keepends=True), autojunk=False).get_opcodes()
        return file_ops[path]

    rows = []
    order = list(new_nodes) + [key for key in old_nodes if key not in new_nodes]
    for key in order:
        old_node, new_node = old_nodes.get(key), new_nodes.get(key)
        row = {'id': key, 'status': 'unchanged',
               'before': node_snapshot(old_node, baseline['workspace'], old_records, frozen=True),
               'after': node_snapshot(new_node, after, new_records),
               'reasons': [], 'code_diffs': [], 'metadata_changed': metadata(old_node) != metadata(new_node), 'impact_paths': []}
        old_sources, new_sources = (old_node or {}).get('sources', []), (new_node or {}).get('sources', [])
        states = []
        if old_node and new_node:
            old_sorted = sorted(old_sources, key=lambda x: (x['path'], x.get('line', 0)))
            new_sorted = sorted(new_sources, key=lambda x: (x['path'], x.get('line', 0)))
            if not old_sorted or len(old_sorted) != len(new_sorted) or [x['path'] for x in old_sorted] != [x['path'] for x in new_sorted]:
                states.append('unresolved')
                row['reasons'].append('来源文件或区间集合发生变化，无法仅凭模型变动确认方法代码变化。')
            else:
                for old_source, new_source in zip(old_sorted, new_sorted):
                    path = old_source['path']
                    old_range = mapped_range(old_source, old_records.get(path, {}), baseline['workspace'])
                    new_range = mapped_range(new_source, new_records.get(path, {}), after)
                    if old_range['state'] != 'available' or new_range['state'] != 'available':
                        states.append('unresolved')
                        row['reasons'].append(path + '：' + old_range.get('reason', new_range.get('reason', '范围未验证。')))
                        continue
                    if old_range['text'] == new_range['text']:
                        states.append('unchanged')
                        if old_source['line'] != new_source['line']:
                            row['reasons'].append(path + '：方法区间仅发生行号移动，内容未改变。')
                        continue
                    ops = operations(path)
                    touched = ops and any(tag != 'equal' and
                                          (overlap(old_range['start'], old_range['end'], i1, i2) or
                                           overlap(new_range['start'], new_range['end'], j1, j2))
                                          for tag, i1, i2, j1, j2 in ops)
                    if touched:
                        states.append('modified')
                        row['reasons'].append(path + '：实际差异与此节点明确登记的源码区间重叠。')
                        row['code_diffs'].append(range_diff(path, old_range['text'], new_range['text']))
                    else:
                        states.append('unresolved')
                        row['reasons'].append(path + '：模型区间改指向其他内容，未找到对应的真实代码编辑。')
        else:
            adding = new_node is not None
            source_rows = new_sources if adding else old_sources
            if not source_rows:
                states.append('unresolved')
                row['reasons'].append('节点只有模型记录，缺少可核对的源码区间。')
            for source in source_rows:
                path = source['path']
                part = mapped_range(source, new_records.get(path, {}) if adding else old_records.get(path, {}),
                                    after if adding else baseline['workspace'])
                ops = operations(path)
                if part['state'] != 'available' or ops is None:
                    states.append('unresolved')
                    row['reasons'].append(path + '：' + part.get('reason', '缺少另一时点源码，不能确认新增或移除。'))
                    continue
                start, end = part['start'], part['end']
                entirely_inserted_or_deleted = any(
                    tag == ('insert' if adding else 'delete') and
                    (j1 if adding else i1) <= start and end <= (j2 if adding else i2)
                    for tag, i1, i2, j1, j2 in ops)
                entirely_equal = any(tag == 'equal' and (j1 if adding else i1) <= start and
                                     end <= (j2 if adding else i2) for tag, i1, i2, j1, j2 in ops)
                if entirely_inserted_or_deleted:
                    states.append('added' if adding else 'removed')
                    row['reasons'].append(path + ('：登记区间完整出现在实际新增行中。' if adding else '：登记区间完整出现在实际删除行中。'))
                    row['code_diffs'].append(range_diff(path, '' if adding else part['text'], part['text'] if adding else ''))
                elif entirely_equal:
                    states.append('unchanged')
                    row['reasons'].append(path + '：只有模型记录增加或移除，对应源码仍存在且内容未变。')
                else:
                    states.append('unresolved')
                    row['reasons'].append(path + '：模型记录增加或移除，但差异不足以确认整个方法新增或删除。')
        # Any missing range keeps the complete node unresolved, while retaining
        # the observed snippet diffs for its other sources.
        row['status'] = ('unresolved' if 'unresolved' in states else
                         'modified' if 'modified' in states else
                         'added' if states and all(x == 'added' for x in states) else
                         'removed' if states and all(x == 'removed' for x in states) else
                         'unresolved' if any(x in {'added', 'removed'} for x in states) else 'unchanged')
        if row['metadata_changed']:
            row['reasons'].append('节点说明或输入输出等模型资料发生变化；该标记本身不代表源码编辑。')
        if not row['reasons']:
            row['reasons'].append('已登记的完整源码区间与任务基线一致。')
        rows.append(row)

    by_id = {row['id']: row for row in rows}
    all_nodes = {**old_nodes, **new_nodes}
    # Retain both old and new edges even when an edge ID changed destination.
    edges = {(e['id'], e['from'], e['to']): e for m in (old_model, model) for e in m['edges']}
    direct = {row['id'] for row in rows if row['status'] in {'modified', 'added', 'removed'}}
    for origin in sorted(direct):
        for direction in ('caller', 'downstream-data'):
            queue = deque([(origin, [origin], [])])
            visited = {origin}
            while queue:
                current, node_path, edge_path = queue.popleft()
                for edge in edges.values():
                    if direction == 'caller' and edge['to'] == current:
                        target = edge['from']
                    elif direction == 'downstream-data' and edge['from'] == current:
                        target = edge['to']
                    else:
                        continue
                    if target in visited:
                        continue
                    visited.add(target)
                    next_nodes, next_edges = node_path + [target], edge_path + [edge['id']]
                    queue.append((target, next_nodes, next_edges))
                    if target not in by_id or (direction == 'downstream-data' and all_nodes[target]['kind'] != 'data'):
                        continue
                    reason = ('此节点通过已登记关系调用或依赖发生编辑的节点，需关联回归。' if direction == 'caller' else
                              '改动方法的下游路径关联此数据节点，行为可能受影响；这不是数据库字段发生修改的证据。')
                    target_row = by_id[target]
                    if len(target_row['impact_paths']) < 20:
                        target_row['impact_paths'].append({'direction': direction, 'nodes': next_nodes, 'edges': next_edges, 'reason': reason})
                    if target_row['status'] == 'unchanged':
                        target_row['status'] = 'affected'
                    if reason not in target_row['reasons']:
                        target_row['reasons'].append(reason)
    graph_before = copy.deepcopy(old_model)
    graph_before['nodes'] = [by_id[node['id']]['before'] for node in old_model['nodes']]
    for edge in graph_before['edges']:
        state = source_state(edge.get('sources', []), baseline['workspace'])
        edge['freshness'] = 'frozen' if state == 'current' else state
    return rows, graph_before


def need(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def read_json(path):
    from project_check import read_json as read
    return read(path)


def write_json(path, data, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def indexed(rows, label):
    need(isinstance(rows, list), f'{label}: expected list')
    result = {}
    for row in rows:
        need(isinstance(row, dict) and nonempty(row.get('id')), f'{label}: missing id')
        need(row['id'] not in result, f'{label}: duplicate id {row["id"]}')
        result[row['id']] = row
    return result


def relative_path(value):
    need(nonempty(value) and '\\' not in value and not any(ord(c) < 32 for c in value), 'Invalid source path')
    path = PurePosixPath(value)
    need(not path.is_absolute() and '..' not in path.parts and path.parts and ':' not in value,
         'Source path must remain inside project')
    need(str(path) == value, 'Source path must be normalized')
    return value


def safe_file(root, value):
    value = relative_path(value)
    root = Path(root).resolve()
    path = root / value
    need(path.resolve().is_relative_to(root), f'Source path escapes project: {value}')
    return path


def sources(rows, label, root=None):
    need(isinstance(rows, list), f'{label}: sources must be a list')
    for row in rows:
        need(isinstance(row, dict), f'{label}: invalid source')
        relative_path(row.get('path'))
        need(isinstance(row.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', row['sha256']),
             f'{label}: source needs the reviewed file sha256')
        if 'line' in row or 'end_line' in row:
            need(type(row.get('line')) is int and row['line'] > 0, f'{label}: invalid source line')
            need(type(row.get('end_line', row['line'])) is int and row.get('end_line', row['line']) >= row['line'],
                 f'{label}: invalid source end_line')
        if root is not None:
            path = safe_file(root, row['path'])
            if path.is_file() and 'line' in row and not path.is_symlink():
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() == row['sha256']:
                    need(row.get('end_line', row['line']) <= len(content.splitlines()), f'{label}: source line outside file')


def observation(row, label, root=None):
    need(row.get('provenance') in PROVENANCE, f'{label}: provenance must be observed/inferred/unknown')
    need(nonempty(row.get('description')), f'{label}: missing description')
    sources(row.get('sources', []), label, root)
    need(row['provenance'] != 'observed' or bool(row.get('sources')), f'{label}: observed claim needs source evidence')


def refs(row, field, allowed, label):
    values = row.get(field, [])
    need(isinstance(values, list) and all(nonempty(x) for x in values), f'{label}: {field} must be a string list')
    need(len(set(values)) == len(values) and set(values) <= set(allowed), f'{label}: invalid {field} reference')
    return values


def validate_model(model, inventory, root=None):
    need(isinstance(model, dict) and model.get('version') == 1, 'model: version must be 1')
    need(nonempty(model.get('title')), 'model: title required')
    modules = indexed(inventory['modules'], 'inventory modules')
    requirements = indexed(inventory.get('requirements', []), 'inventory requirements')
    scenarios = indexed(inventory['scenarios'], 'inventory scenarios')
    features = indexed(model.get('features'), 'features')
    nodes = indexed(model.get('nodes'), 'nodes')
    edges = indexed(model.get('edges'), 'edges')
    screens = indexed(model.get('screens'), 'screens')
    for key, feature in features.items():
        need(feature.get('module') in modules and nonempty(feature.get('name')), f'{key}: invalid feature module/name')
        need(nonempty(feature.get('description')), f'{key}: missing description')
        need(feature.get('implementation') in {'unknown', 'partial', 'implemented'}, f'{key}: invalid implementation assessment')
        for field, allowed in [('node_ids', nodes), ('screen_ids', screens), ('requirement_ids', requirements), ('scenario_ids', scenarios)]:
            refs(feature, field, allowed, key)
        if 'entry_node_ids' in feature:
            refs(feature, 'entry_node_ids', feature.get('node_ids', []), key)
        for requirement in feature.get('requirement_ids', []):
            need(requirements[requirement]['module'] == feature['module'] and requirements[requirement]['state'] == 'active',
                 f'{key}: requirement module/state mismatch')
        for scenario in feature.get('scenario_ids', []):
            need(scenarios[scenario]['module'] == feature['module'] and scenarios[scenario]['state'] == 'active',
                 f'{key}: scenario module/state mismatch')
        for screen in feature.get('screen_ids', []):
            need(screens[screen].get('feature') == key, f'{key}: screen belongs to another feature')
    for key, node in nodes.items():
        need(node.get('kind') in NODE_KINDS and nonempty(node.get('name')), f'{key}: invalid node kind/name')
        observation(node, key, root)
        if 'symbol' in node:
            need(nonempty(node['symbol']), f'{key}: symbol must be nonempty text')
        for field in ('steps', 'errors'):
            if field in node:
                need(isinstance(node[field], list) and all(nonempty(x) for x in node[field]), f'{key}: {field} must be a text list')
        for field in ('inputs', 'outputs'):
            if field in node:
                need(isinstance(node[field], list), f'{key}: {field} must be a list')
                seen = set()
                for item in node[field]:
                    need(isinstance(item, dict) and all(nonempty(item.get(name)) for name in ('name', 'type', 'description')),
                         f'{key}: {field} requires name, type and description')
                    need(item['name'] not in seen, f'{key}: duplicate {field} name')
                    seen.add(item['name'])
        if 'fields' in node:
            need(node['kind'] == 'data' and isinstance(node['fields'], list), f'{key}: fields require a data node and list')
            field_names = set()
            for field in node['fields']:
                need(isinstance(field, dict) and all(nonempty(field.get(name)) for name in ('name', 'type', 'description')),
                     f'{key}: field requires name, type and description')
                need(field['name'] not in field_names, f'{key}: duplicate field {field["name"]}')
                field_names.add(field['name'])
                need('primary_key' not in field or type(field['primary_key']) is bool, f'{key}: primary_key must be boolean')
    for key, edge in edges.items():
        need(edge.get('from') in nodes and edge.get('to') in nodes, f'{key}: unknown edge endpoint')
        need(edge.get('kind') in EDGE_KINDS, f'{key}: invalid edge kind')
        observation(edge, key, root)
        if edge['kind'] == 'references':
            need(all(nodes[edge[end]]['kind'] == 'data' for end in ('from', 'to')), f'{key}: references requires data endpoints')
        if 'relationship' in edge:
            relationship = edge['relationship']
            need(edge['kind'] == 'references' and isinstance(relationship, dict), f'{key}: relationship requires references edge')
            need(relationship.get('cardinality') in {'one-to-one', 'one-to-many', 'many-to-one', 'many-to-many', 'unknown'},
                 f'{key}: invalid relationship cardinality')
            need(relationship.get('constraint') in {'foreign-key', 'logical', 'unknown'}, f'{key}: invalid relationship constraint')
            need(('from_field' in relationship) == ('to_field' in relationship), f'{key}: provide both relationship fields')
            for end in ('from', 'to'):
                field_key = end + '_field'
                if field_key in relationship:
                    need(nonempty(relationship[field_key]), f'{key}: invalid {field_key}')
                    node = nodes[edge[end]]
                    if 'fields' in node:
                        need(relationship[field_key] in {f['name'] for f in node['fields']}, f'{key}: unknown {field_key}')
            if relationship['constraint'] == 'foreign-key':
                need(edge['provenance'] == 'observed' and edge.get('sources') and 'from_field' in relationship,
                     f'{key}: enforced foreign-key requires observed source evidence and field references')
    for key, screen in screens.items():
        feature = features.get(screen.get('feature'))
        need(feature is not None and key in feature.get('screen_ids', []), f'{key}: screen must be linked from feature')
        need(nonempty(screen.get('title')), f'{key}: missing title')
        observation(screen, key, root)
        fields = screen.get('fields', [])
        need(isinstance(fields, list), f'{key}: fields must be a list')
        for field in fields:
            need(isinstance(field, dict) and nonempty(field.get('label')) and field.get('kind') in {'input', 'text', 'select'},
                 f'{key}: invalid schematic field')
        for hotspot_id, hotspot in indexed(screen.get('hotspots', []), key + ': hotspots').items():
            need(nonempty(hotspot.get('label')) and nonempty(hotspot.get('description')), f'{hotspot_id}: missing explanation')
            need(hotspot.get('node_id') in nodes, f'{hotspot_id}: unknown hotspot node')
            need(hotspot['node_id'] in feature.get('node_ids', []), f'{hotspot_id}: hotspot node not linked to feature')
            refs(hotspot, 'scenario_ids', feature.get('scenario_ids', []), hotspot_id)
    if 'discovery' in model:
        need(isinstance(model['discovery'], dict), 'discovery must be an object')
        for item in model['discovery'].get('files', []):
            need(isinstance(item, dict), 'discovery: invalid file')
            relative_path(item.get('path'))
    return model


def discover(root, inventory, title):
    """Index requirements/files without inventing architecture or completed work."""
    snapshot = workspace_state.capture(root)
    features = []
    for module in inventory['modules']:
        requirements = [r for r in inventory.get('requirements', []) if r['module'] == module['id'] and r['state'] == 'active']
        groups = requirements or [{'id': 'module:' + module['id'], 'title': module['name']}]
        for requirement in groups:
            requirement_ids = [requirement['id']] if requirements else []
            scenario_ids = [s['id'] for s in inventory['scenarios'] if s['module'] == module['id'] and s['state'] == 'active'
                            and (not requirements or requirement['id'] in s.get('requirement_ids', []))]
            features.append({'id': 'feature:' + requirement['id'], 'module': module['id'], 'name': requirement['title'],
                             'description': '来自验收清单的入口；实现、页面与代码关系尚未分析。', 'implementation': 'unknown',
                             'requirement_ids': requirement_ids, 'scenario_ids': scenario_ids, 'node_ids': [], 'screen_ids': []})
    return {'version': 1, 'title': title, 'features': features, 'nodes': [], 'edges': [], 'screens': [],
            'discovery': {'created_at': now(), 'method': 'inventory-and-file-index',
                          'limitations': ['文件列表不能证明调用关系。阅读代码后补充节点、关系和线框原型；草稿不是完整架构。'],
                          'files': [{'path': path, 'kind': 'unknown', 'sha256': item.get('sha256')}
                                    for path, item in snapshot['files'].items() if item.get('kind') == 'file']}}


def source_state(rows, snapshot):
    if not rows:
        return 'unknown'
    return 'current' if all(snapshot['files'].get(row['path'], {}).get('sha256') == row['sha256'] and
                            snapshot['files'].get(row['path'], {}).get('kind') == 'file' for row in rows) else 'stale'


def mapped_paths(model):
    return {s['path'] for field in ('nodes', 'edges', 'screens') for row in model[field] for s in row.get('sources', [])}


def impact(before, after, diff):
    """Traverse callers/dependents backwards, retaining removed baseline nodes."""
    paths = {row['path'] for row in diff}
    direct = {n['id'] for m in (before, after) for n in m['nodes'] if any(s['path'] in paths for s in n.get('sources', []))}
    edges = [e for m in (before, after) for e in m['edges']]
    for edge in edges:
        if any(s['path'] in paths for s in edge.get('sources', [])):
            direct.update((edge['from'], edge['to']))
    reverse = {}
    for edge in edges:
        reverse.setdefault(edge['to'], set()).add(edge['from'])
    affected, queue = set(direct), deque(sorted(direct))
    while queue:
        for caller in sorted(reverse.get(queue.popleft(), [])):
            if caller not in affected:
                affected.add(caller)
                queue.append(caller)
    feature_rows = []
    features = {f['id']: f for m in (before, after) for f in m['features']}
    for key, feature in features.items():
        linked = {node for m in (before, after) for f in m['features'] if f['id'] == key for node in f.get('node_ids', [])}
        screen_changed = any(s['feature'] == key and any(source['path'] in paths for source in s.get('sources', []))
                             for m in (before, after) for s in m['screens'])
        if linked & affected or screen_changed:
            feature_rows.append({'id': key, 'name': feature['name'], 'reason': 'direct' if linked & direct or screen_changed else 'dependency',
                                 'nodes': sorted(linked & affected), 'scenario_ids': feature.get('scenario_ids', []),
                                 'removed': key not in {f['id'] for f in after['features']}})
    return {'direct_nodes': sorted(direct), 'dependent_nodes': sorted(affected - direct), 'features': feature_rows,
            'unmapped_changes': [row for row in diff if row['path'] not in mapped_paths(before) | mapped_paths(after)],
            'limitations': ['影响范围基于已登记关系；未映射文件及遗漏关系需要继续分析。',
                            '调用链包含 agent 复核或推断的关系；影响传播不是实际运行证明。']}


def validate_notes(value, models):
    need(isinstance(value, dict), 'notes: expected object')
    need(isinstance(value.get('notes'), list), 'notes: expected notes list')
    ids = {'feature': {r['id'] for m in models for r in m['features']}, 'node': {r['id'] for m in models for r in m['nodes']}}
    result = []
    for note in value['notes']:
        need(isinstance(note, dict) and note.get('target_type') in ids, 'notes: target_type must be feature/node')
        need(note.get('target') in ids[note['target_type']], 'notes: unknown target')
        need(note.get('kind') in {'behavior', 'rationale', 'learning', 'deviation', 'unknown'} and nonempty(note.get('text')),
             'notes: invalid kind/text')
        result.append({**note, 'provenance': 'agent-authored', 'verified': False})
    return result


def acceptance(root, inventory, run_dir):
    if run_dir is None:
        return {'state': 'not-run', 'message': '未指定验收运行；实现描述不能代表测试通过。', 'results': [], 'gaps': []}
    from project_check import summarize, validate_inventory, verified_manifest, artifact_ok
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    need(run_dir.is_relative_to(root), 'map: run directory must remain inside project')
    frozen = validate_inventory(read_json(run_dir / 'inventory.json'))
    payload = read_json(run_dir / 'results.json')
    scope = payload.get('run', {}).get('scope', 'all')
    module = scope[len('module:'):] if scope.startswith('module:') else None
    summary = summarize(frozen, payload, run_dir, module)
    entries, problems = verified_manifest(payload.get('run', {}).get('snapshot_manifest'), run_dir)
    workspace_verified = payload.get('version') == 2 and not problems and artifact_ok('workspace.json', run_dir, entries)
    current = workspace_state.current(read_json(run_dir / 'workspace.json'), root) if workspace_verified else None
    same_inventory = frozen == inventory
    fresh = current is not None and current['current'] and same_inventory
    rows = copy.deepcopy(summary['results'])
    for row in rows:
        row['historical_status'] = row['status']
        if not fresh:
            row['status'] = 'stale'
        row['evidence'] = [str((run_dir / p).relative_to(root)) for p in row['evidence'] if safe_file(run_dir, p).is_file()]
    return {'state': 'current' if fresh else 'stale', 'outcome': summary['outcome'], 'run_id': summary['run']['id'],
            'run_path': str(run_dir.relative_to(root)), 'results': rows, 'gaps': summary['gaps'],
            'message': '验收证据对应当前代码与清单。' if fresh else '验收仅适用于历史快照；当前源码或清单已变化，或缺少源码快照。',
            'workspace': current, 'workspace_verified': bool(workspace_verified), 'inventory_current': same_inventory,
            'snapshot_problems': problems + ([] if workspace_verified else ['workspace.json 未包含在有效的冻结快照清单中。'])}


def feature_acceptance(feature, inventory, evidence):
    expected = set(feature.get('scenario_ids', []))
    requirements = set(feature.get('requirement_ids', []))
    required = {s['id'] for s in inventory['scenarios'] if s['state'] == 'active' and requirements & set(s.get('requirement_ids', []))}
    omitted = sorted(required - expected)
    rows = {r['id']: r for r in evidence['results']}
    statuses = [rows.get(key, {}).get('status', 'not-run') for key in expected]
    state = ('failed' if 'failed' in statuses else 'stale' if 'stale' in statuses else
             'passed' if expected and requirements and not omitted and all(x == 'passed' for x in statuses) and
             evidence.get('state') == 'current' and evidence.get('outcome') == 'PASSED' else 'incomplete')
    return {'state': state, 'omitted_scenarios': omitted, 'scenario_count': len(expected),
            'passed': statuses.count('passed'), 'unknown': sum(x in {'not-run', 'stale', 'blocked', 'skipped'} for x in statuses)}


def link_file(root, source, view_dir):
    path = safe_file(root, source)
    if not path.is_file() or path.is_symlink():
        return None
    return quote(os.path.relpath(path, Path(view_dir).resolve()).replace(os.sep, '/'), safe='/')


def view_data(root, model, inventory, evidence, review=None):
    root = Path(root).resolve()
    snapshot = workspace_state.capture(root)
    rendered = copy.deepcopy(model)
    current_text = {path: source_text(root, path, snapshot) for path in mapped_paths(model)}
    rendered['nodes'] = [node_snapshot(node, snapshot, current_text) for node in model['nodes']]
    view_dir = root / '.project-check' / 'views'
    for field in ('nodes', 'edges', 'screens'):
        for row in rendered[field]:
            row['freshness'] = source_state(row.get('sources', []), snapshot)
            for source in row.get('sources', []):
                source['href'] = link_file(root, source['path'], view_dir)
    for feature in rendered['features']:
        feature['acceptance'] = feature_acceptance(feature, inventory, evidence)
        linked = set(feature.get('node_ids', []))
        pending = list(linked)
        while pending:
            key = pending.pop()
            for edge in rendered['edges']:
                if edge['from'] == key and edge['to'] not in linked:
                    linked.add(edge['to'])
                    pending.append(edge['to'])
        relevant = [node for node in rendered['nodes'] if node['id'] in linked]
        relevant += [edge for edge in rendered['edges'] if edge['from'] in linked]
        relevant += [screen for screen in rendered['screens'] if screen['feature'] == feature['id']]
        feature['analysis_state'] = ('stale' if any(row['freshness'] == 'stale' for row in relevant) else
                                     'unknown' if not relevant or any(row['provenance'] == 'unknown' or row['freshness'] == 'unknown' for row in relevant)
                                     else 'inferred' if any(row['provenance'] == 'inferred' for row in relevant) else 'reviewed')
    for row in evidence['results']:
        row['links'] = [{'path': path, 'href': link_file(root, path, view_dir)} for path in row.get('evidence', [])]
    rendered_review = copy.deepcopy(review)
    if rendered_review:
        for change in rendered_review.get('node_changes', []):
            for source in (change.get('after') or {}).get('sources', []):
                source['href'] = link_file(root, source['path'], view_dir)
    known = mapped_paths(model)
    files = [{'path': path, 'mapped': path in known} for path, item in snapshot['files'].items() if item.get('kind') == 'file']
    mapped_requirements = {key for feature in model['features'] for key in feature.get('requirement_ids', [])}
    mapped_scenarios = {key for feature in model['features'] for key in feature.get('scenario_ids', [])}
    return {'version': 1, 'generated_at': now(), 'snapshot': {'revision': snapshot['revision'], 'fingerprint': snapshot['fingerprint']},
            'model': rendered, 'inventory': inventory, 'acceptance': evidence, 'review': rendered_review,
            'coverage': {'files': files, 'mapped_files': sum(x['mapped'] for x in files), 'total_files': len(files),
                         'unmapped_requirements': [r['id'] for r in inventory.get('requirements', []) if r['state'] == 'active' and r['id'] not in mapped_requirements],
                         'unmapped_scenarios': [s['id'] for s in inventory['scenarios'] if s['state'] == 'active' and s['id'] not in mapped_scenarios]},
            'limitations': ['此离线页面只反映生成时的状态。代码继续变化后，请重新执行 map/review。',
                            '原型用于导航和解释，不执行产品操作；点击成功不代表真实功能验收通过。',
                            '实现评估、说明与关系由 agent 复核或推断；文件身份、代码差异和验收结果分别展示。']}


def render_html(data):
    template = (Path(__file__).resolve().parent.parent / 'assets' / 'project-map.html').read_text(encoding='utf-8')
    encoded = json.dumps(data, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    encoded = encoded.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    need(template.count('__PROJECT_CHECK_DATA__') == 1, 'map template: expected one data placeholder')
    return template.replace('__PROJECT_CHECK_DATA__', encoded)


def write_view(root, model, inventory, run_dir=None, review=None):
    evidence = acceptance(root, inventory, run_dir)
    data = view_data(root, model, inventory, evidence, review)
    directory = Path(root) / '.project-check' / 'views'
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / 'project.json', data)
    (directory / 'project.html').write_text(render_html(data), encoding='utf-8')
    return {'view': str(directory / 'project.html'), 'data': str(directory / 'project.json'),
            'features': len(model['features']), 'nodes': len(model['nodes']), 'acceptance': evidence['state']}


def change_path(root, change_id):
    need(isinstance(change_id, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', change_id),
         'baseline/review requires a safe --change-id (letters, digits, hyphen, underscore, dot)')
    return Path(root) / '.project-check' / 'changes' / change_id


def handle(command, root, *, change_id=None, run_dir=None, notes=None):
    from project_check import validate_inventory
    root = Path(root).resolve()
    config = root / '.project-check'
    need(config.resolve().is_relative_to(root), 'project-check configuration escapes project root')
    inventory = validate_inventory(read_json(config / 'inventory.json'))
    model_path = config / 'model.json'
    if not model_path.is_file():
        project = read_json(config / 'project.json')
        model = discover(root, inventory, project.get('name') or root.name)
        write_json(model_path, model, exclusive=True)
    model = validate_model(read_json(model_path), inventory, root)
    if command == 'map':
        return write_view(root, model, inventory, run_dir)
    need(command in {'baseline', 'review'}, f'Unknown project map command: {command}')
    directory = change_path(root, change_id)
    baseline_path = directory / 'baseline.json'
    if command == 'baseline':
        snapshot = {'version': 1, 'change_id': change_id, 'created_at': now(), 'workspace': workspace_state.capture(root),
                    'model': model, 'inventory': inventory}
        snapshot['source_text'] = capture_text(root, model, snapshot['workspace'])
        write_json(baseline_path, snapshot, exclusive=True)
        return {'baseline': str(baseline_path), 'fingerprint': snapshot['workspace']['fingerprint'],
                'message': '已记录现有代码状态；此前未提交的修改不会被当成本次新增修改。'}
    before = read_json(baseline_path)
    need(before.get('version') == 1 and before.get('change_id') == change_id, 'Invalid change baseline')
    validate_model(before['model'], before['inventory'])
    after = workspace_state.capture(root)
    diff = workspace_state.changes(before['workspace'], after)
    supplied_notes = read_json(notes) if isinstance(notes, (str, Path)) else notes
    node_changes, graph_before = method_changes(root, before, model, after)
    review = {'version': 1, 'change_id': change_id, 'created_at': now(), 'baseline_at': before['created_at'],
              'before_fingerprint': before['workspace']['fingerprint'], 'after_fingerprint': after['fingerprint'],
              'changes': diff, 'impact': impact(before['model'], model, diff),
              'code_diffs': code_diffs(root, before, model, after, diff),
              'node_changes': node_changes, 'graph_before': graph_before,
              'reviewer_notes': validate_notes(supplied_notes, [before['model'], model]) if supplied_notes is not None else [],
              'learning_state': 'unread', 'learning_blocks_archive': False}
    directory.mkdir(parents=True, exist_ok=True)
    history = directory / ('review-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    write_json(history, review, exclusive=True)
    write_json(directory / 'review.json', review)
    view = write_view(root, model, inventory, run_dir, review)
    return {**view, 'review': str(directory / 'review.json'), 'history': str(history), 'changed_files': len(diff),
            'affected_features': len(review['impact']['features']), 'unmapped_changes': len(review['impact']['unmapped_changes'])}
