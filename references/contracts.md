# 数据约定与工具

Python 工具只依赖标准库，要求 Python 3.9+。它不会安装依赖、启动应用、替 agent 编写业务测试或解析所有框架报告。先通过项目自己的 runner 实际运行，再用小型适配代码转换原生结果。

## project.json

版本 1。初始化模板在 `assets/`。命令是记录，不由本工具执行。

```json
{
  "version": 1,
  "name": "customer-portal",
  "services": [{
    "id": "api",
    "cwd": ".",
    "start": ["./scripts/start-test-api.sh"],
    "ready_url": "http://127.0.0.1:8000/health",
    "identity_check": "核对健康响应的项目标识和本次代码版本",
    "depends_on": [],
    "timeout_seconds": 60
  }],
  "required_env": ["TEST_USER_PASSWORD"],
  "data_setup": [{"cwd": ".", "argv": ["./scripts/seed-test-data.sh"]}],
  "data_cleanup": [{"cwd": ".", "argv": ["./scripts/clean-test-data.sh"]}],
  "runners": [{
    "id": "browser",
    "cwd": ".",
    "argv": ["npx", "playwright", "test"],
    "discovery": ["npx", "playwright", "test", "--list"],
    "selection": "以场景 ID 过滤测试标题",
    "native_report": "Playwright JSON reporter",
    "requires_services": ["api"]
  }]
}
```

示例命令必须替换为项目实际命令。静态项目允许没有后端服务。运行器需要添加项目原生报告输出参数；不要假定示例 argv 已完整配置 reporter。

## inventory.json：唯一场景清单

```json
{
  "version": 1,
  "modules": [{"id": "customers", "name": "客户管理", "regression_dependencies": []}],
  "scenarios": [{
    "id": "CUSTOMERS-001",
    "module": "customers",
    "title": "从列表创建客户并持久保存",
    "kind": "e2e",
    "priority": "critical",
    "state": "active",
    "basis": {"status": "confirmed", "source": "docs/requirements.md 中的客户创建验收标准"},
    "preconditions": ["具备创建权限的测试账号已登录", "使用本次运行隔离数据"],
    "actions": ["点击列表的新建按钮", "填写必填信息", "点击保存", "刷新列表"],
    "expected": ["新记录显示正确", "刷新后记录仍存在", "不存在未解释的运行错误"],
    "coverage": "implemented",
    "runner": "browser",
    "tests": ["tests/customers.spec.ts::CUSTOMERS-001"],
    "dependency_mode": "real"
  }]
}
```

- 模块关联通过 `regression_dependencies` 声明，允许循环，选择器会去重。服务依赖不应有循环。
- `kind`：`unit/integration/browser/e2e/visual`。
- `basis.status`：`confirmed/inferred/unknown`。源码推断不能标 confirmed；未确认时保留覆盖缺口。
- `coverage`：`implemented/missing`。有测试文件不证明测试通过；缺失时 tests 可空、runner 可省略。
- `state`：`active/retired`；retired 必填 `retirement_reason`，不参与执行。
- `tests`：稳定的原生用例映射 ID，可包括浏览器/参数化变体；适配器必须保留相同规范化规则。每条场景的所有必需映射都要执行。一个测试支持多个场景时分别验证各自断言。
- `dependency_mode`：`real/mocked/none`。没有外部依赖用 none；端到端 e2e 必须 real，模拟流程改为 browser/integration，避免虚假联调结论。

## results.json：每次运行一份

证据路径相对 results.json 所在目录，必须在该目录内指向实际文件。避免文件路径穿越或将截图当作原生用例报告。文件存在仅是最低校验，agent/CI 还需核验其内容。

```json
{
  "version": 1,
  "run": {
    "id": "20260929T100000Z",
    "started_at": "2026-09-29T10:00:00Z",
    "finished_at": "2026-09-29T10:01:00Z",
    "revision": "commit SHA 或 non-git",
    "workspace": "clean 或变更摘要及指纹",
    "environment": "本地隔离数据库；Chromium；桌面视口",
    "scope": "module:customers",
    "selected_ids": ["CUSTOMERS-001"],
    "native_reports": ["native-report.json"]
  },
  "results": [{
    "id": "CUSTOMERS-001",
    "status": "passed",
    "reason": "",
    "dependency_mode": "real",
    "observed_tests": [{"id": "tests/customers.spec.ts::CUSTOMERS-001", "status": "passed"}],
    "assertions_checked": true,
    "runtime_errors_checked": true,
    "unexpected_errors": [],
    "flaky": false,
    "evidence": ["native-report.json"],
    "interaction_evidence": ["browser-trace.zip"]
  }]
}
```

结果状态 `passed/failed/blocked/skipped`。失败、阻塞、跳过必填原因。遗漏场景由汇总器补成 blocked；缺少通过证据也改为 blocked；意外运行错误、失败子用例或 flaky 改为 failed。每次运行不得存在重复场景/子用例 ID；未知 ID 或错范围直接拒绝。

原生报告缺失时整个结果不完整。预期未确认或覆盖未实现，即使部分测试成功，整体也不完整。状态优先级：FAILED > INCOMPLETE > PASSED。退出码：0 / 1 / 2（通过 / 未完全通过 / 输入无效）。

## 工具命令

```sh
python3 <skill-dir>/scripts/project_check.py init --root <project>
python3 <skill-dir>/scripts/project_check.py validate --root <project>
python3 <skill-dir>/scripts/project_check.py checklist --root <project>
python3 <skill-dir>/scripts/project_check.py select --root <project> --module customers
python3 <skill-dir>/scripts/project_check.py report --root <project> --results <run-dir>/results.json --inventory <run-dir>/inventory.json --module customers --out <run-dir>/REPORT.md
```

`init` 只创建缺失文件。`checklist` 写入生成视图 `.project-check/CHECKLIST.md`。`select` 输出 JSON，包含 scope 和 selected_ids。`report` 默认只向 stdout 输出，不覆盖已有报告文件；需要新报告时使用新的 run 目录。
