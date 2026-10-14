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

// These fixtures exercise presentation only; native business evidence is tested separately.
async function taskPage(page: any, overrides: any = {}, context?: any) {
  const scenario = { id: 'SAVE', title: '保存后刷新仍可读取', actions: ['点击保存', '刷新页面'], expected: ['订单仍存在'] };
  const fixture = {
    ...data,
    inventory: { ...data.inventory, scenarios: [scenario, { id: 'PAY', title: '付款流程' }] },
    task: { id: 'save-order', goal: '保存订单失败时保留输入，成功后刷新仍可读取', scope: 'task:save-order', status: 'passed',
      requirements: [{ id: 'REQ-SAVE', title: '保存成功后刷新仍可读取' }], scenarios: [scenario],
      changes: [{ id: 'save', name: 'save()', status: 'modified', before: '直接保存订单', after: '校验后保存订单',
        rationale: ['先校验输入，避免写入无效订单'], notes: [] }, { id: 'caller', name: 'caller()', status: 'affected' }],
      impact: { features: [{ id: 'orders', name: '订单保存' }], unmapped_changes: [{ path: 'unmapped.py' }] },
      unselected_ids: ['PAY'], gaps: [] },
    acceptance: { state: 'current', evidence_valid: true, scope: 'task:save-order', outcome: 'PASSED', run_id: 'task-run',
      run: { revision: 'task-source-version' }, workspace: { expected: 'frozen-source-fingerprint' },
      message: '验收证据对应当前代码与清单。', results: [{ id: 'SAVE', status: 'passed',
        links: [{ href: './evidence/report.json', path: 'evidence/report.json' }] }] },
  };
  Object.assign(fixture, overrides);
  const template = await readFile(resolve('assets/project-map.html'), 'utf8');
  const html = template.replace('__PROJECT_CHECK_DATA__', JSON.stringify(fixture).replace(/</g, '\\u003c'));
  if (context) {
    await context.route('http://project-check-ui.local/**', (route: any) => route.fulfill({
      contentType: route.request().url().endsWith('.json') ? 'application/json' : 'text/html',
      body: route.request().url().endsWith('.json') ? '{"scope":"task:save-order","status":"passed"}' : html,
    }));
    await page.goto('http://project-check-ui.local/report/');
  } else await page.setContent(html);
}

test('task-home-and-method-before-after', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await taskPage(page);
  await expect(page.getByRole('heading', { name: '本次任务', exact: true })).toBeVisible();
  await expect(page.locator('.task-verdict')).toContainText('已通过');
  await expect(page.locator('.task-comparison')).toContainText('直接保存订单');
  await expect(page.locator('.task-comparison')).toContainText('校验后保存订单');
  await expect(page.locator('.task-change')).toContainText('先校验输入');
  expect(await page.evaluate(() => {
    const head = document.querySelector('.view-head')!.getBoundingClientRect();
    const subtitle = document.querySelector('.view-subtitle')!.getBoundingClientRect();
    const content = document.querySelector('.task-content')!.getBoundingClientRect();
    return head.bottom >= subtitle.bottom && content.top >= head.bottom;
  })).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('task-summary-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '查看方法与代码对比' }).click();
  await expect(page.locator('#detail')).toContainText('原来做什么');
  await page.getByRole('button', { name: '代码对比', exact: true }).click();
  await expect(page.locator('.diff-add')).toContainText('validate(order)');
  await page.getByRole('tab', { name: '本次任务', exact: true }).click();
  await expect(page.locator('.task-verdict')).toContainText('已通过');
  expect(errors).toEqual([]);
});

test('task-evidence-link-and-unverified-range', async ({ page, context }) => {
  await taskPage(page, {}, context);
  await page.getByText('查看未纳入本次验收的范围', { exact: true }).click();
  await expect(page.locator('.task-content')).toContainText('付款流程');
  await page.getByText('查看操作、预期与证据', { exact: true }).click();
  const popupPromise = context.waitForEvent('page');
  await page.getByRole('link', { name: 'evidence/report.json', exact: true }).click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  await expect(popup).toHaveURL('http://project-check-ui.local/report/evidence/report.json');
  await expect(popup.locator('body')).toContainText('task:save-order');
  await popup.close();
  await page.getByText('查看版本与原始验收信息', { exact: true }).click();
  await expect(page.locator('.task-content')).toContainText('task-source-version');
  await expect(page.locator('.task-content')).toContainText('frozen-source-fingerprint');
});

test('task-pending-and-failed-states-preserve-raw-result', async ({ page }) => {
  await taskPage(page);
  const current = await page.evaluate(() => JSON.parse(document.getElementById('project-data')!.textContent!));
  current.task.status = 'pending';
  current.task.gaps = ['源码已经变化，需要重新验收'];
  current.acceptance.state = 'stale';
  current.acceptance.results[0].historical_status = 'passed';
  const template = await readFile(resolve('assets/project-map.html'), 'utf8');
  await page.setContent(template.replace('__PROJECT_CHECK_DATA__', JSON.stringify(current)));
  await expect(page.locator('.task-verdict')).toContainText('待验收');
  await expect(page.locator('.scenario-heading')).toContainText('待验收');
  await expect(page.locator('.task-content')).toContainText('需要重新验收');
  await page.getByText('查看操作、预期与证据', { exact: true }).click();
  await expect(page.locator('.task-scenarios')).toContainText('历史结果：通过');
  current.task.status = 'failed';
  current.acceptance.state = 'current';
  current.acceptance.results[0].status = 'failed';
  await page.setContent(template.replace('__PROJECT_CHECK_DATA__', JSON.stringify(current)));
  await expect(page.locator('.task-verdict')).toContainText('未通过');
});

test('task-mobile-keyboard-and-empty-changes', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await taskPage(page);
  await expect(page.locator('.task-comparison')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('task-summary-mobile.png'), fullPage: true });
  await page.getByRole('tab', { name: '本次任务', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: '关系图', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Home');
  await expect(page.getByRole('tab', { name: '本次任务', exact: true })).toHaveAttribute('aria-selected', 'true');
  const current = await page.evaluate(() => JSON.parse(document.getElementById('project-data')!.textContent!));
  current.task.changes = [];
  current.task.status = 'pending';
  const template = await readFile(resolve('assets/project-map.html'), 'utf8');
  await page.setContent(template.replace('__PROJECT_CHECK_DATA__', JSON.stringify(current)));
  await expect(page.locator('.task-content')).toContainText('尚无可精确定位的代码改动');
});
