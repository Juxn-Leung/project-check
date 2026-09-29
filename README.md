# Project Check · 项目验收

为 Codex 提供可复用的项目测试流程：建立测试清单、执行全量验收、测试指定模块，并交付真实操作和运行错误的证据。

面向 Web 前端及其后端、数据库。Skill 可以个人安装一份；各项目的测试配置、清单和测试代码独立保存在各自仓库。

## 解决什么问题

- 页面能打开，不代表按钮、表单和导航能够正常使用。
- 构建成功，不代表页面没有运行异常或接口错误。
- 测试命令返回成功，不代表全部场景实际执行。

Project Check 要求把已复核需求映射到验收场景，交互场景实际点击入口并检查业务结果，同时收集页面异常、控制台错误和请求失败。未映射需求、缺失执行结果、缺少交互证据、跳过或阻塞不能汇总成无条件通过。

## 安装

这是 Codex Skill，不是 VS Code 扩展。可在 Codex 中使用，也可由 VS Code 中的 Codex 扩展调用。

### npm 分发（发布准备中）

npm 包提供显式的 Skill 安装命令，不会在 `npm install` 时通过安装钩子自动修改用户目录。Node.js 需要 20 或更新版本。

拟发布包名为 `project-check-skill`，Skill 名称仍为 `project-check`。下列注册表安装命令需在首次发布完成后使用；当前可先使用文末的本地安装方式。

个人安装：

```sh
npx project-check-skill@latest install --user
```

仅安装到当前项目：

```sh
npx project-check-skill@latest install --project .
```

可追加 `--dry-run` 预览目标位置。目标已存在时拒绝覆盖，以保留本地修改。安装后仍在 Codex 聊天中使用下方三个入口；此安装器本身不执行业务测试。

也可把发布后的包固定为开发依赖，再显式执行安装：

```sh
npm install --save-dev --save-exact project-check-skill@latest
npx project-check install --project .
```

仅执行 `npm install` 会将包放进 `node_modules`，还需要第二步才能把 Skill 放到 Codex 可发现的位置。首次发布前可从本地仓库执行 `node bin/project-check.js install --project <目标项目目录>`。

### 从 GitHub 安装

个人安装后可供多个项目使用。首次安装，确认目标目录尚不存在，然后执行：

```sh
mkdir -p "$HOME/.agents/skills"
git clone --branch master https://github.com/Juxn-Leung/project-check.git "$HOME/.agents/skills/project-check"
```

如果只希望在某个项目中使用，将整个 Skill 目录放到该项目的 `.agents/skills/project-check/`。个人安装与项目安装选一种即可。

在 Codex 中确认能找到 `project-check`；如果尚未识别，重新启动 Codex。辅助脚本需要 Python 3.9+，仅依赖标准库。业务测试依赖按目标项目实际技术栈准备。

## 三个入口

在目标项目的 Codex 聊天输入框中输入，以下不是终端命令，也不是新注册的斜杠命令。

**建立或更新测试：**

```text
使用 $project-check build，为当前项目建立测试清单，补齐测试，并试运行验证。
```

**执行全量测试：**

```text
使用 $project-check run，启动需要的前后端服务，执行全部测试清单并生成报告。
```

**测试指定模块：**

```text
使用 $project-check module 用户管理，测试该模块及必要的关联场景，生成报告。
```

执行入口默认报告产品缺陷。需要修改产品代码时，显式添加“修复并重跑”；修复前的失败报告仍保留。

## 项目内的测试资产

首次接入由 Codex 识别实际启动命令、服务依赖、现有测试框架和业务模块，生成或补齐：

```text
目标项目/
├── .project-check/
│   ├── project.json       # 服务、环境变量名、测试运行器
│   ├── inventory.json     # 需求来源、需求及验收场景映射
│   ├── CHECKLIST.md       # 从清单生成的阅读视图
│   └── runs/             # 每次运行的报告、日志和证据
└── tests/                 # 示例；沿用项目现有测试目录
```

配置、清单、测试代码随业务项目管理；运行产物和认证状态按项目策略忽略或归档。配置只记录秘密的环境变量名，不保存凭据值。

全量执行指运行当前清单中的全部场景，不代表已覆盖全部未知功能。报告区分通过、失败、阻塞、跳过，并显示未映射需求、来源待复核、未实现或预期未确认的场景。

## 工具边界

`scripts/project_check.py` 提供初始化、清单校验、模块选择、运行快照、pytest/Playwright 报告导入和报告汇总。它不会自行扫描业务、启动服务或运行测试；这些动作由 Codex 按 Skill 说明和项目实际工具完成。原生报告和附件须在下一轮运行前冻结，具体命令见 [数据约定](references/contracts.md)。

报告脚本会检查需求映射、来源复核快照、原生用例结果和证据 SHA-256，但不能证明业务断言本身充分。Codex 必须核对测试代码与原生报告；需要强制合并门禁时，应由 CI 执行实际测试并生成结果。Playwright 项目可复制随包提供的阶段错误 fixture，精确区分登出成功响应、预期 401 与请求取消。

安装 Skill 不会自动在每次代码修改后运行测试。首次使用建议选择一个真实项目，完成接入并核验关键流程。

## 开发与验证

在本仓库根目录执行：

```sh
python3 -m unittest discover -s tests -v
npm test
python3 scripts/project_check.py --help
```

当前回归测试验证清单和报告工具的行为，不代表已经在任意业务项目上完成端到端验收。

## 点击发布

本仓库已提供 [Publish npm package](https://github.com/Juxn-Leung/project-check/actions/workflows/publish.yml) 工作流。首次发布 npm 包并配置 Trusted Publisher 后，日常只需推送代码，在 GitHub 的 **Run workflow** 中选择 `patch / minor / major` 并运行。

流程自动测试、打包、安装验证、发布到 npm，并同步 `master` 的版本及 Git 标签。无需保存长期 npm token。勾选 `dry_run` 可先预演，预演不会发布或推送。

首次配置的准确字段、认证边界及失败恢复见 [发布指南](docs/publishing.md)。工作流已加入仓库不代表 npm 信任关系已配置或真实发布已验证。

完整工作流见 [SKILL.md](SKILL.md)。
