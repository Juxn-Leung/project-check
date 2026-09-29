# 数据约定与工具

辅助脚本依赖 Python 3.9+ 标准库，不会启动服务或测试。`project.json` 保持版本 1，记录项目实际命令和依赖；示例见 `assets/project.template.json`。`inventory.json` 新建时用版本 2；旧版 1 可读取，但报告一律 `INCOMPLETE`，直到完成需求审阅和证据迁移。

## inventory.json v2

```json
{
  "version": 2,
  "modules": [{"id": "customers", "name": "客户管理", "regression_dependencies": []}],
  "requirement_sources": [{
    "id": "brief", "kind": "file", "modules": ["customers"], "path": "docs/requirements.md",
    "sha256": "填写文件内容的 64 位小写 SHA-256", "reviewed": true
  }],
  "requirements": [{
    "id": "REQ-CUSTOMER-1", "module": "customers", "title": "创建客户后可重新查询",
    "source": "brief", "state": "active"
  }],
  "scenarios": [{
    "id": "CUSTOMERS-001", "module": "customers", "title": "创建并刷新后查询客户",
    "state": "active", "kind": "e2e", "requirement_ids": ["REQ-CUSTOMER-1"],
    "basis": {"status": "confirmed", "source": "docs/requirements.md"},
    "preconditions": ["使用隔离测试账号"], "actions": ["点击新建", "保存", "刷新"],
    "expected": ["记录仍存在"], "coverage": "implemented", "runner": "browser",
    "tests": ["chromium::tests/customers.spec.ts::CUSTOMERS-001"], "dependency_mode": "real"
  }]
}
```

`requirement_sources` 用 `modules` 明确来源覆盖的模块；即使来源暂未提取出需求，其变化也会阻止这些模块得到完整结论。`file` 来源使用相对项目根目录的路径和真实 SHA-256。外部或口头确认的来源使用 `{"kind":"external","modules":["customers"],"locator":"...","revision":"...","reviewed":true}`；版本与复核状态需要由执行者确认。`reviewed` 不能靠脚本自动推断。需求及场景的 `active/retired` 保留历史；退役项必须写 `retirement_reason`。有效需求只由同模块的活动场景映射。每个所选模块都需要登记有效需求；未登记、未映射、来源未复核或本次开始时文件哈希变化，报告都是 `INCOMPLETE`。

原场景字段仍适用：`kind` 为 `unit/integration/browser/e2e/visual`，`basis.status` 为 `confirmed/inferred/unknown`，`coverage` 为 `implemented/missing`，`dependency_mode` 为 `real/mocked/none`。e2e 必须使用真实依赖。`tests` 写适配器规范化后的完整用例 ID；一个场景的全部映射都须执行并通过。

## 运行快照与内置适配器

```sh
python3 <skill-dir>/scripts/project_check.py snapshot --root <project> --run-dir <run-dir>
python3 <skill-dir>/scripts/project_check.py adapt --root <project> --run-dir <run-dir> \
  --runner browser --framework playwright-json --native <playwright-json> --artifact-root <runner工作目录>
python3 <skill-dir>/scripts/project_check.py adapt --root <project> --run-dir <run-dir> \
  --runner unit --framework pytest-junit --native <junit.xml> --artifact-root <runner工作目录>
```

`snapshot` 新建唯一运行目录，冻结 `inventory.json`、`project.json`、`source-review.json` 及其 `snapshot-manifest.json` 哈希清单。必须在测试开始前执行。`adapt` 在每个 runner 结束后、下一轮运行前立即执行；它复制原生报告和 Playwright 附件，输出 `native/<runner>.*`、`observations/<runner>.json`、`evidence/<runner>.json` 及 `artifacts/<runner>/`。适配器不会覆盖已有同名产物。`--artifact-root` 是允许读取附件的目录；越界、丢失或空附件写入用例 `issues`，不会被悄悄忽略。每份 evidence 清单包含冻结文件的 SHA-256。

pytest 使用内置 JUnit XML 输出（`pytest --junitxml=<path>`），用例 ID 是 `classname::name`，参数化后缀保留。Playwright 使用 JSON reporter（例如 `PLAYWRIGHT_JSON_OUTPUT_FILE=<path> npx playwright test --reporter=json`），用例 ID 是 `projectName::file::describe标题::测试标题`；无项目名时以 `default` 代替。对照真实适配输出填写清单。适配器读取失败、跳过、重试和附件；若 pytest 使用重试插件，而 XML 无法证明每次尝试，应补充可核验的尝试记录，不能把最终绿色当作稳定通过。原生报告无法证明的业务断言、真实依赖和 UI 状态仍须审阅。

## results.json v2

`adapt` 只生成原生观察结果，不代替场景层面的验收判断。按其标准输出路径填写：

```json
{
  "version": 2,
  "run": {
    "id": "20260929T100000Z", "started_at": "2026-09-29T10:00:00Z",
    "finished_at": "2026-09-29T10:01:00Z", "revision": "commit SHA 或 non-git",
    "workspace": "clean 或变更摘要", "environment": "隔离数据库；Chromium",
    "scope": "module:customers", "selected_ids": ["CUSTOMERS-001"],
    "source_review": "source-review.json", "snapshot_manifest": "snapshot-manifest.json",
    "native_reports": ["native/browser.json"],
    "observations": ["observations/browser.json"],
    "evidence_manifests": ["evidence/browser.json"]
  },
  "results": [{
    "id": "CUSTOMERS-001", "status": "passed", "reason": "",
    "dependency_mode": "real", "assertions_checked": true,
    "unexpected_errors": [], "evidence": [], "interaction_evidence": []
  }]
}
```

`observed_tests`、`flaky` 和浏览器 `runtime_errors_checked` 在 v2 中来自适配器，不接受手填字段代替原生观察。`assertions_checked` 仍须在核对测试断言后明确设置；`evidence` 可增加其他已冻结附件，但其路径也必须列入 evidence 清单。浏览器事件日志或截图本身不算操作轨迹；通过需要 trace、Playwright 操作步骤或明确的操作记录附件。缺少原生用例或冻结附件、哈希不符、缺少浏览器事件记录会阻塞声称通过的场景；原生失败、重试后通过或未解释的运行错误仍为失败。

报告使用运行目录里的清单快照和来源审阅快照，历史结果不会随当前需求文件变化而改写。报告只证明已登记来源和场景的覆盖，不证明未知需求不存在。退出码仍为 `0/1/2`（通过／失败或不完整／输入无效）。
