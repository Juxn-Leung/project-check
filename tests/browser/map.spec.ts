import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

// A controlled UI fixture: business acceptance remains separate from map navigation.
const source = [{ path: 'src/orders.py', line: 10, end_line: 14, href: './src/orders.py' }];
const node = (id: string, description: string, kind = 'function') => ({
  id, kind, name: description, symbol: id + '()', description,
  provenance: 'observed', freshness: 'current', sources: source,
  inputs: [{ name: 'order', type: 'Order', description: '要保存的订单' }],
  outputs: [{ name: 'result', type: 'Order', description: '保存后的订单' }],
  steps: ['校验输入', '保存并返回结果'], errors: ['保存失败时返回错误'],
});
const before = [node('caller', '发起保存'), node('save', '直接保存订单'), node('old', '旧保存逻辑'),
  { ...node('data', '订单数据', 'data'), inputs: [], outputs: [], fields: [{ name: 'customer_id', type: 'integer', description: '订单所属客户' }] },
  node('unrelated', '无关方法')];
const after = before.filter(n => n.id !== 'old').map(n => n.id === 'save' ? { ...n, description: '校验后保存订单' } : n);
after.push(node('validate', '检查订单输入'));
const edge = (id: string, from: string, to: string, kind = 'calls') => ({ id, from, to, kind, description: '真实登记关系', provenance: 'observed', sources: source });
const oldEdges = [edge('caller-save', 'caller', 'save'), edge('save-target', 'save', 'old'), edge('save-data', 'save', 'data', 'writes')];
const newEdges = [edge('caller-save', 'caller', 'save'), edge('save-target', 'save', 'validate'), edge('save-data', 'save', 'data', 'writes')];
const feature = (id: string, name: string, ids: string[]) => ({ id, name, module: 'orders', description: name + '的功能说明', node_ids: ids, entry_node_ids: [ids[0]], requirement_ids: [], scenario_ids: [], screen_ids: [], acceptance: { state: 'not-run' } });
const features = [feature('orders', '订单保存', ['save', 'caller', 'validate', 'data']), feature('other', '其他功能', ['unrelated'])];
const snapshot = (n: any, frozen = false) => n ? { ...n, code_snippets: [{ path: 'src/orders.py', line: 10, end_line: 14, state: frozen ? 'frozen' : 'current', code: frozen ? 'def save(order):\n    return store(order)' : 'def save(order):\n    validate(order)\n    return store(order)' }] } : null;
const data = {
  model: { title: '关系图回归项目', features, nodes: after, edges: newEdges, screens: [] },
  inventory: { modules: [], requirements: [], scenarios: [] },
  acceptance: { state: 'not-run', results: [], message: '尚未执行产品验收' },
  generated_at: '2026-10-05', snapshot: { revision: 'fixture' }, coverage: { mapped_files: 1, total_files: 1 },
  review: {
    change_id: 'save-order', graph_before: { features, nodes: before, edges: oldEdges },
    reviewer_notes: [], impact: { unmapped_changes: [{ path: 'unmapped.py' }] },
    node_changes: [...new Set([...before, ...after].map(n => n.id))].map(id => ({
      id, status: ({ save: 'modified', old: 'removed', validate: 'added', caller: 'affected', data: 'affected' } as any)[id] || 'unchanged',
      before: snapshot(before.find(n => n.id === id), true), after: snapshot(after.find(n => n.id === id)),
      reasons: ['方法范围与任务起点比较'], impact_paths: [],
      code_diffs: id === 'save' ? [{ path: 'src/orders.py', available: true, diff: '@@ -1,2 +1,3 @@\n def save(order):\n+    validate(order)\n     return store(order)' }] : [],
    })),
  },
};

test.beforeEach(async ({ page }) => {
  const template = await readFile(resolve('assets/project-map.html'), 'utf8');
  await page.setContent(template.replace('__PROJECT_CHECK_DATA__', JSON.stringify(data).replace(/</g, '\\u003c')));
});

test('map-focus-and-method-navigation', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await expect(page.getByRole('heading', { name: '本次修改', exact: true })).toBeVisible();
  await expect(page.locator('.graph-node')).toHaveCount(4);
  await expect(page.locator('[data-node="save"]')).toBeVisible();
  await expect(page.locator('#detail')).toContainText('校验后保存订单');
  await page.getByLabel('聚焦所选方法').uncheck();
  await expect(page.locator('.graph-node')).toHaveCount(5);
  await page.getByRole('combobox', { name: '定位改动节点' }).selectOption('validate');
  await expect(page.locator('#detail')).toContainText('检查订单输入');
  await expect(page.locator('.graph-node')).toHaveCount(2);
  await page.locator('[data-node="save"]').click();
  await expect(page.locator('.graph-node')).toHaveCount(4);
  expect(errors).toEqual([]);
});

test('map-before-after-preserves-retargeted-and-removed-relations', async ({ page }) => {
  await page.getByLabel('聚焦所选方法').uncheck();
  await expect(page.locator('.graph-edge[data-from="save"][data-to="validate"]')).toHaveCount(1);
  await page.getByRole('button', { name: '修改前', exact: true }).click();
  await expect(page.locator('.graph-edge[data-from="save"][data-to="old"]')).toHaveCount(1);
  await expect(page.locator('.graph-edge[data-from="save"][data-to="validate"]')).toHaveCount(0);
  await expect(page.locator('[data-node="validate"]')).toHaveClass(/ghost/);
  await page.getByRole('button', { name: '修改后', exact: true }).click();
  await expect(page.locator('[data-node="old"]')).toHaveClass(/ghost/);
  await expect(page.locator('.graph-edge[data-from="save"][data-to="validate"]')).toHaveCount(1);
});

test('map-code-diff-and-unverified-tests', async ({ page }) => {
  await page.getByRole('button', { name: '代码对比', exact: true }).click();
  await expect(page.locator('#detail')).toContainText('修改前的实现');
  await expect(page.locator('#detail')).toContainText('修改后的实现');
  await expect(page.locator('.diff-add')).toContainText('validate(order)');
  await page.getByRole('button', { name: '测试与回归', exact: true }).click();
  await expect(page.locator('#detail')).toContainText('不能据此确认功能完成');
  await expect(page.locator('#detail')).toContainText('尚未执行产品验收');
});

test('map-tables-search-and-field-location', async ({ page }) => {
  await page.getByRole('tab', { name: '功能表', exact: true }).click();
  await expect(page.locator('.record-table tbody tr')).toHaveCount(2);
  await page.getByRole('button', { name: '订单保存', exact: true }).click();
  await expect(page.getByRole('heading', { name: '订单保存 · 本次修改', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '全部功能', exact: false }).click();
  await page.getByRole('tab', { name: '字段表', exact: true }).click();
  await page.getByRole('searchbox', { name: '筛选字段表' }).fill('customer_id');
  await expect(page.locator('.record-table tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: 'customer_id', exact: true }).click();
  await expect(page.locator('.mini-table tr.highlighted')).toContainText('customer_id');
  await expect(page.locator('#detail')).toContainText('订单所属客户');
});

test('map-overview-keyboard-and-narrow-screen', async ({ page }) => {
  await page.setViewportSize({ width: 600, height: 900 });
  await page.getByRole('tab', { name: '关系图', exact: true }).click();
  await expect(page.locator('.graph-node.feature-node')).toHaveCount(2);
  await page.getByRole('button', { name: '适应画布', exact: true }).click();
  await page.locator('[data-node="orders"]').click();
  await expect(page.locator('[data-node="save"]')).toBeVisible();
  await page.getByRole('tab', { name: '关系图', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: '功能表', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.record-table')).toBeVisible();
});
