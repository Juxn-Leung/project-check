# 统一执行与恢复

适用于已经配置好测试命令的项目。由 Codex 完成配置、断言审阅和调用，使用者只需给出任务。`execute` 会选择任务、检查环境、冻结输入、执行命令、导入证据、生成报告和摘要；通过且源码未变时自动封存。发现任务基线时自动生成前后讲解页。它不自动修改产品或归档 OpenSpec。

```sh
python3 <skill-dir>/scripts/project_check.py execute --root <project> --task <id> --assessment <project>/.project-check/assessment.json --notes <notes.json>
```

`--notes` 可省略，结构沿用 map.md。`--module` 与 `--task` 互斥；两者都省略表示全清单。输出 JSON 提供 `run_dir`、`summary`、`view`、实际结果与缺口。用户读取 `SUMMARY.md` 或任务页即可；原生证据仍保留在运行目录。

## 配置现有运行器

沿用 project.json v1 的命令，每个需要托管执行的 runner 增加 `framework`，并让报告路径使用 `{run_dir}`。命令仍为 argv 数组，不经过 shell。支持 `{run_dir}`、`{run_id}`、`{native_report}`；子进程也会收到 `PROJECT_CHECK_RUN_DIR` 和 `PROJECT_CHECK_RUN_ID`。

```json
{
  "id": "unit",
  "cwd": ".",
  "argv": ["python3", "-m", "pytest", "--junitxml={native_report}"],
  "discovery": ["python3", "-m", "pytest", "--collect-only", "-q"],
  "selection": "运行现有完整测试套件",
  "native_report": "{run_dir}/raw/unit.xml",
  "framework": "pytest-junit",
  "requires_services": [],
  "timeout_seconds": 120
}
```

`framework` 当前支持 `pytest-junit`、`playwright-json`。`artifact_root` 默认 `{run_dir}`，可配置为项目内实际附件目录。Playwright 配置应同时使用错误 fixture、动作 reporter 和 JSON reporter，并从 `PROJECT_CHECK_RUN_DIR` 构造报告及附件路径。

runner 的 argv 决定实际执行范围；不会把自然语言 `selection` 猜成过滤参数。运行全套测试是允许的，清单外的失败仍保留。首次用真实报告建立场景映射：`adapt` 的输出现在直接列出规范化 `test_ids`。Playwright 的文件路径取决于 testDir，不能照抄示例中的 `tests/`。

缺少测试、依赖或原生报告会保持待验收。进程返回非零，即使原生报告显示通过也不能通过。每轮报告写入独立目录，避免上一轮产物被错误采用。

## 断言审阅

命令通过只能证明测试运行成功。Codex 阅读测试与需求后，登记实际依赖模式、对应断言及测试源码哈希：

```json
{
  "version": 1,
  "scenarios": [{
    "id": "CUSTOMERS-001",
    "tests": ["tests.test_customers::test_create"],
    "test_sources": [{"path": "tests/test_customers.py", "sha256": "<实际 SHA-256>"}],
    "assertions": ["重新查询数据库并断言客户名称等于输入，核对保存持久化要求"],
    "dependency_mode": "real"
  }]
}
```

`tests` 必须匹配场景的全部用例，`test_sources` 至少包含实际断言所在文件。缺少审阅或测试源码变化时仍可运行，但不会生成验收通过。不能仅为消除阻塞填写审阅。该文件被冻结为本轮 `assessment.json`；哈希校验保证对应版本，不证明业务断言充分。

## 服务与测试数据

`requires_services` 控制服务依赖。自动启动的服务沿用 `start/cwd/depends_on/ready_url/timeout_seconds`，额外提供 `identity_argv`：它必须实际核对服务身份，成功返回 0，例如检查健康接口的项目名和测试版本。仅端口可访问不足以确认身份。

执行器按依赖启动本轮拥有的进程，等待健康与身份检查，结束后清理自身进程。依赖启动失败仍运行独立 runner。现有测试框架已管理服务时继续沿用框架，无需重复配置。

托管服务的进程组清理目前支持 macOS/Linux。Windows 项目可由已有测试框架管理服务；执行器会拒绝其自身尚不能保证清理的托管服务配置。强制结束执行器或机器掉电不保证自动清理，恢复时需要核对旧进程和数据。

`data_setup/data_cleanup` 执行项目明确登记的隔离数据命令，留下回执。不要把生产迁移或整库删除放进这些数组。清理失败会保留待验收状态及原因。

## 中断后恢复

```sh
python3 <skill-dir>/scripts/project_check.py execute --root <project> --task <id> --run-dir <此前输出的 run_dir> --resume
```

恢复先核对源码、配置、范围和冻结证据。已经有完成回执的命令不会重复运行；已完成的原生导入可复用，随后补齐报告、封存和视图。原来的 assessment 自动沿用。

命令被中断且没有结束回执时，无法确定是否已产生副作用，执行器会停止并指出日志位置；检查对应进程和数据后新建运行。源码变化、证据损坏或部分导入也需要新建运行。旧运行保留，不能靠修改历史报告补证据。

`execution.json` 是阶段日志，`commands/*.json` 是命令回执，`SUMMARY.md` 是面向使用者的摘要。退出码为 0（通过）、1（未通过或待验收）、2（输入错误或无法安全恢复）。
