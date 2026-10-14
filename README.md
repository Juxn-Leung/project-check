# Project Check · 项目理解与验收

让 Codex 用功能关系图、功能表和字段表解释项目。修改时直接查看哪些方法变了、原来与现在分别做什么，以及沿调用关系可能影响哪些功能和数据。真实操作证据用于验收与 OpenSpec 收尾。

支持 Web 前端、后端、数据库及 CLI 等代码项目。Skill 可以个人安装一份；各项目的测试配置、清单和测试代码独立保存在各自仓库。

## 解决什么问题

- 看不懂 AI 写的代码：从功能进入真实方法，查看中文用途、输入输出、执行步骤和调用连线；字段表解释数据含义与外键关系。
- 不清楚改动影响：在同一张关系图中标出直接修改和关联影响，点击方法对照任务基线代码，并解释行为变化。
- OpenSpec 改动堆积：把技术验收、明确要求的业务签收与学习阅读状态分开。
- 页面能打开，不代表按钮、表单和导航能够正常使用。
- 构建成功，不代表页面没有运行异常或接口错误。
- 测试命令返回成功，不代表全部场景实际执行。

Project Check 要求把已复核需求映射到验收场景，交互场景实际点击入口并检查业务结果，同时收集页面异常、控制台错误和请求失败。未映射需求、缺失执行结果、缺少交互证据、跳过或阻塞不能汇总成无条件通过。

## 安装

这是 Codex Skill，不是 VS Code 扩展。可在 Codex 中使用，也可由 VS Code 中的 Codex 扩展调用。

### 从 npm 安装

npm 包提供显式的 Skill 安装命令，不会在 `npm install` 时通过安装钩子自动修改用户目录。Node.js 需要 20 或更新版本。

包名为 `project-check-skill`，Skill 名称为 `project-check`。包已发布到 npm，可直接使用下列安装命令。

个人安装：

```sh
npx project-check-skill@latest install --user
```

仅安装到当前项目：

```sh
npx project-check-skill@latest install --project .
```

可追加 `--dry-run` 预览目标位置。目标已存在时拒绝覆盖，以保留本地修改。安装后仍在 Codex 聊天中使用下方入口；此安装器本身不执行业务测试。

也可把包固定为开发依赖，再显式执行安装：

```sh
npm install --save-dev --save-exact project-check-skill@latest
npx project-check install --project .
```

仅执行 `npm install` 会将包放进 `node_modules`，还需要第二步才能把 Skill 放到 Codex 可发现的位置。本地开发时也可执行 `node bin/project-check.js install --project <目标项目目录>`。

### 从 GitHub 安装

个人安装后可供多个项目使用。首次安装，确认目标目录尚不存在，然后执行：

```sh
mkdir -p "$HOME/.agents/skills"
git clone --branch master https://github.com/Juxn-Leung/project-check.git "$HOME/.agents/skills/project-check"
```

如果只希望在某个项目中使用，将整个 Skill 目录放到该项目的 `.agents/skills/project-check/`。个人安装与项目安装选一种即可。

在 Codex 中确认能找到 `project-check`；如果尚未识别，重新启动 Codex。辅助脚本需要 Python 3.9+，仅依赖标准库。业务测试依赖按目标项目实际技术栈准备。

## 在 Codex 中使用

在目标项目的聊天输入框中输入，以下是自然语言入口，不是终端命令或注册的斜杠命令。Codex 负责分析项目、维护关联数据、执行辅助命令和打开结果。

```text
使用 $project-check map，分析当前项目，生成关系图、功能表和字段表，解释关键方法的用途、输入输出和调用关系。
```

```text
使用 $project-check，修改订单功能前定位相关方法和字段并记录基线；修改后用关系图标出变化，解释每个改动方法前后有什么不同、影响哪里，并实际验收。
```

```text
使用 $project-check finish，验收 OpenSpec 的 <change-id>，满足已约定的自动归档条件后收尾。
```

仍支持 `build` 建立/更新测试，`run` 执行清单全部场景，`module 用户管理` 验收指定模块及关联场景，`review` 解释本次修改。

只要求测试时默认报告产品缺陷；当前任务已授权实现或修复时，继续在授权范围内修复并重跑。保留修复前证据，不要求重复说出某个口令。

首次接入时，Codex 阅读已有需求、路由、页面、接口和数据模型，把来源与解释登记到模型中。`map` 辅助脚本本身仅能建立保守的清单/文件索引并渲染 HTML，不能单靠文件名理解完整业务。图中明确区分已阅读来源、推断、未知及过期关联。不会凭空生成完成百分比。

生成页以关系图为中心，功能表和字段表都能定位到具体节点。点击方法查看职责、参数、返回值、调用方与数据读写；有改动记录时默认进入“本次修改”，聚焦所选方法及直接关联，可从下拉列表定位改动节点、切换基线和当前关系、展开实际代码差异。取消聚焦可查看完整影响图。测试状态和来源证据在详情里查看。

方法修改依据保存的源码范围和内容对比。没有足够源码依据的节点会标记为“无法精确定位”；调用链上的方法和数据会显示为“可能受影响”，不会因为同属一个文件就声称全部被改动。方法用途等语义说明仍需 Codex 阅读实际代码后填写，脚本不会凭方法名编造解释。

一次改动的顺序：需求与关联建模 → `baseline` → 修改代码与测试 → `snapshot` → 执行测试并 `adapt` → `report` / `seal` → `review` / `map` → 按策略 `finish`。这些是 Codex 的内部步骤，用户可以只给出一次完整任务。

## 项目内的测试资产

首次接入由 Codex 识别实际启动命令、服务依赖、现有测试框架和业务模块，生成或补齐：

```text
目标项目/
├── .project-check/
│   ├── project.json       # 服务、环境变量名、测试运行器
│   ├── inventory.json     # 需求来源、需求及验收场景映射
│   ├── model.json        # 功能、方法说明、字段及调用关系；复用清单 ID
│   ├── CHECKLIST.md       # 从清单生成的阅读视图
│   ├── views/project.html # 可点击的项目地图
│   ├── changes/          # 修改前基线、实际代码差异及学习讲解
│   └── runs/             # 每次运行的报告、日志、源码身份及归档回执
└── tests/                 # 示例；沿用项目现有测试目录
```

配置、清单、测试代码随业务项目管理；运行产物和认证状态按项目策略忽略或归档。配置只记录秘密的环境变量名，不保存凭据值。

全量执行指运行当前清单中的全部场景，不代表已覆盖全部未知功能。报告区分通过、失败、阻塞、跳过，并显示未映射需求、来源待复核、未实现或预期未确认的场景。

## 工具边界

`scripts/project_check.py` 提供初始化、清单校验、模块选择、项目地图、任务基线与差异、运行快照、pytest/Playwright 报告导入、报告汇总、证据封存及 OpenSpec 收尾。它不会自行扫描业务、启动服务或运行测试；这些动作由 Codex 按 Skill 说明和项目实际工具完成。原生报告和附件须在下一轮运行前冻结，具体命令见 [数据约定](references/contracts.md)。

报告脚本会检查需求映射、来源复核快照、原生用例结果和证据 SHA-256，但不能证明业务断言本身充分。Codex 必须核对测试代码与原生报告；需要强制合并门禁时，应由 CI 执行实际测试并生成结果。Playwright 项目需要同时接入自动运行的阶段错误 fixture 和动作 reporter：原生 API 操作及 `expect` 必须与场景 `checks` 对应，只有导航、空步骤或预期失败不能作为验收通过。具体接入见 [浏览器验收](references/browser.md)。

安装 Skill 不会自动在每次代码修改后运行测试。首次使用建议选择一个真实项目，完成接入并核验关键流程。

## OpenSpec 与旧项目迁移

OpenSpec 保存规格，Project Check 复用需求来源并补充执行证据；HTML 是生成视图，不再维护第二份需求正文。`finish` 默认只预检，执行归档需要已保存的 `completion.auto_archive: true` 和 `--execute`。只有明确规定需要人工签收的改动才要求对应确认；用户还没读代码讲解不影响技术收尾。历史证据冻结，来源变化或无法识别的自定义规格会明确阻塞。配置与命令见 [OpenSpec 收尾](references/openspec.md)。

旧版清单和报告仍可读取；旧浏览器场景缺少 `checks` 时不会继续无条件通过。补齐检查点、更新 fixture/reporter 并重新执行；不要通过修改旧报告补证据。旧运行没有 `workspace.json` 时可保留为历史，但不能用于新版本的封存/自动归档。

安装 skill 不会监听每次文件保存。任务中显式使用它，或在项目既有开发约定里要求修改前建基线、完成后验收和更新视图。地图只反映生成时状态，需要重新生成才能反映后续修改。

## 开发与验证

在本仓库根目录执行：

```sh
python3 -m unittest discover -s tests -v
npm test
python3 scripts/project_check.py --help
```

这些回归验证工具行为，不代表已经验收任意业务项目。安装 Playwright 后，还可运行 `npm run test:reporter`，使用真实测试运行器验证报告附件与断言识别，无需启动浏览器。

可选的真实 Chromium 回归需要本机允许浏览器进程启动，并安装 `@playwright/test` 和 Chromium。依赖可放在独立目录；不会加入发布包。设置 `PROJECT_CHECK_PLAYWRIGHT_ROOT` 指向包含依赖的目录，`PLAYWRIGHT_BROWSERS_PATH` 按 Playwright 的浏览器安装位置配置，然后执行：

```sh
npm run test:browser
```

该回归会启动隔离的本地前后端，实际填写、保存、刷新并读取数据，同时注入控制台、页面和网络错误。原生测试故意包含失败，工具最终须把九种场景正确归类，才算工具回归成功。报告、trace 和后端数据保存在输出目录；进程启动或环境失败不能冒充通过。无需业务账号，不使用真实业务数据库。

`npm run test:map` 单独验证图表交互：方法聚焦、前后关系及删除节点、代码差异、功能/字段表定位、键盘与窄屏操作。使用受控 UI 数据，不能代替真实项目的业务验收；CI 同时运行这三组浏览器回归并保存证据。

## 点击发布

本仓库已提供 [Publish npm package](https://github.com/Juxn-Leung/project-check/actions/workflows/publish.yml) 工作流。首次发布 npm 包并配置 Trusted Publisher 后，日常只需推送代码，在 GitHub 的 **Run workflow** 中选择 `patch / minor / major` 并运行。

流程自动测试、打包、安装验证、发布到 npm，并同步 `master` 的版本及 Git 标签。无需保存长期 npm token。勾选 `dry_run` 可先预演，预演不会发布或推送。

首次配置的准确字段、认证边界及失败恢复见 [发布指南](docs/publishing.md)。工作流已加入仓库不代表 npm 信任关系已配置或真实发布已验证。

完整工作流见 [SKILL.md](SKILL.md)。
