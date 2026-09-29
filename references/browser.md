# 浏览器验收

## 建立共享 fixture

在访问页面前装好事件收集，并覆盖新标签页/弹窗，直至交互和异步结果完成：
- 未捕获 JS 异常（如 Playwright 的 `pageerror`）。
- 控制台 `error`，必要时记录与场景有关的警告。
- 传输失败请求，以及 HTTP 错误响应。HTTP 4xx/5xx 通常不属于传输失败事件，需单独收集 response。
- 页面可见错误状态，例如失败 toast、错误边界、无法结束的 loading；由业务断言显式检查。

测试结束时对未解释错误进行断言，不能只 attach 日志却让测试保持绿色。即使用例中途失败，也保存已捕获错误和附件。避免循环监听、重复计数；新页面的监听在触发打开之前安装。

明确接口失败的影响：关键业务请求失败令关联场景失败；无关第三方噪声需给出依据再排除。复制 `assets/playwright/phase-errors.ts`、`phase-rule-engine.mjs` 和声明文件到项目测试目录，从该 fixture 导入 `test`。通过 `phase.run(阶段名, 规则, 操作, 状态断言)` 显式划定操作与异步结果窗口；规则必须写事件类型、方法、URL 正则、精确状态或失败原因、匹配理由及允许次数。窗口外或未匹配的错误仍失败，且 fixture 会在失败时附上完整事件记录。

登出可分别声明：`POST /logout` 的 `response 204` 为必需成功响应（`min: 1`）；登出期间受保护资料请求的 `response 401` 可以允许（`min: 0`）；导航取消的指定请求 `requestfailed` 与精确 `errorText` 可以允许。状态断言需确认已退出登录和会话失效。其它 401、取消请求或错误响应不能被这组规则忽略。若请求可能晚于状态断言完成，先用条件等待覆盖其最终事件，再结束阶段。规则变化应审阅，不为当前失败临时扩大。

```ts
import { test, expect } from './phase-errors';

test('AUTH-LOGOUT-001', async ({ page, phase }) => {
  await phase.run('logout', [
    { kind: 'response', method: 'POST', urlPattern: '/logout$', status: 204,
      reason: 'logout endpoint completed', min: 1, max: 1 },
    { kind: 'response', method: 'GET', urlPattern: '/profile$', status: 401,
      reason: 'protected request after logout', min: 0 },
    { kind: 'requestfailed', method: 'GET', urlPattern: '/feed$', errorText: 'net::ERR_ABORTED',
      reason: 'navigation canceled old feed', min: 0 },
  ],
  async () => { await page.getByRole('button', { name: '退出登录' }).click(); },
  async () => { await expect(page.getByRole('link', { name: '登录' })).toBeVisible(); });
});
```

## 操作与断言

- 按场景从实际页面入口进入；若测试导航/按钮，不能用 `goto` 绕过它。
- 优先使用角色、可访问名称、label 或稳定 test ID；避免与业务无关的脆弱 CSS 层级。
- 使用工具的条件等待和可重试断言，不靠固定 sleep 或强制点击掩盖不可操作状态。
- 每个操作断言可观察后果。按钮存在、截图生成、URL 可访问都不能代替交互成功。
- 保存场景检查最终持久化，不仅检查 toast；过滤/分页检查实际结果，权限场景检查服务端限制。
- 测试运行不执行未经授权的真实支付、邮件/消息发送或外部删除；采用隔离环境，报告模拟边界。

## 前端场景证据

原生 runner 报告应能核对场景 ID、执行的用例、失败信息和断言。为 `browser/e2e/visual` 保存 trace 或等价的操作与断言记录，提供失败截图；敏感信息使用测试数据并在分享前脱敏。

Playwright 适配器会冻结 `project-check-browser-events` 附件，并据此设置 `runtime_errors_checked`、未解释事件和交互证据。v2 `results.json` 中不能手填 `runtime_errors_checked: true` 冒充 fixture。成功场景仍需 trace 或等价的操作与断言步骤证据；缺少事件记录或交互证据会阻塞通过。

视觉测试固定浏览器、操作系统、视口、字体和动态数据策略。使用已有认可基准，意外差异保持失败；不在执行入口自动更新快照。像素一致只说明与基准接近，不证明设计质量。
