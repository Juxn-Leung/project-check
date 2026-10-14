# 项目地图与改动讲解

地图以关系图、功能表和字段表为入口。点击功能展开真实方法与数据，点击方法查看中文用途、输入输出、关键步骤、调用方和代码。修改后默认进入“本次修改”，聚焦所选方法及直接关联；可以从下拉列表定位改动节点，取消聚焦查看整个影响图。它是本地 HTML；语义解释由 agent 阅读实际实现完成，脚本负责引用校验、源码身份、影响传播、差异及页面生成。

## 日常工作顺序

1. 读取项目规则和现有 `.project-check/inventory.json`。沿用模块、需求、场景 ID；不存在清单时先使用 `build` 完成最小接入。
2. 首次执行 `map` 获得保守草稿。脚本只从清单建立功能入口和文件索引，所有未分析关系保留未知。
3. agent 阅读每个功能的真实入口、调用链、数据实体及测试，编辑 `.project-check/model.json`。优先完整分析用户当前要改的业务链，不把文件名当成调用证据。
4. 登记关键方法的真实名称、职责、参数、返回值和完整源码范围。关系图、功能表和字段表共用同一份模型。已有页面原型可以保留作补充，但项目没有页面时直接展示代码职责。
5. 修改前运行 `baseline --change-id <id>`，记录当前工作区，已有未提交修改包含在基线中。ID 可以和 OpenSpec change ID 一致。
6. 执行用户授权的修改和真实验收。模型、清单、测试发生变化时，先补充必要关联，再冻结测试快照；不要在验收后随意修改模型而继续把旧快照当成当前验证。
7. 重新阅读受影响来源后更新模型的来源哈希与说明。不要只为了消除“过期”标记而机械刷新哈希。保留未验证、推断和覆盖缺口。
8. 准备中文改动讲解，运行 `review --change-id <id> --run-dir <run-dir> --notes <notes.json>`。代码、测试、模型仍在变化时先完成这些变化，再执行最终验收和 review。

命令示例（`<skill-dir>` 替换为技能安装目录，`<root>` 替换为项目根目录）：

```sh
python3 <skill-dir>/scripts/project_check.py map --root <root>
python3 <skill-dir>/scripts/project_check.py baseline --root <root> --change-id update-order
python3 <skill-dir>/scripts/project_check.py review --root <root> --change-id update-order --run-dir <root>/.project-check/runs/order-01 --notes <root>/.project-check/changes/update-order/notes.json
```

生成的 `.project-check/views/project.html` 可离线打开。对应 `project.json` 可供其他展示方式复用。每次 `map`/`review` 都重建视图，不覆盖已维护的 `model.json`。普通 `map` 不自动选取历史结果，必须显式提供 `--run-dir` 才加载验收证据。需要保留本次变更视图时重新运行对应的 `review`。

## 一份关联数据

`model.json` 顶层为 `version: 1`、`title`、`features`、`nodes`、`edges`、`screens`。需求正文及场景预期直接读取 `inventory.json`，不要复制到另一套独立文档。

### 功能 features

每个功能包含稳定 `id`、现有模块 `module`、中文 `name`、`description`、`implementation`（`unknown` / `partial` / `implemented`），以及以下 ID 列表：

- `requirement_ids`：已有活动需求；必须属于当前模块。
- `scenario_ids`：已有活动场景；必须属于当前模块。
- `node_ids`：该功能的代码入口或相关节点。
- `entry_node_ids`：可选的入口节点列表，必须属于 `node_ids`；功能表优先从这些方法进入。
- `screen_ids`：该功能的线框界面；界面必须反向指向该功能。

`implementation` 是 agent 的实现评估，独立于执行结果。存在代码、提供描述或点击原型都不能把验收状态变成通过。页面没有完成百分比。登记到同一需求却未被功能引用的活动场景会显示遗漏，阻止该功能通过。

### 节点 nodes 与来源

节点包含 `id`、`kind`、`name`、`description`、`provenance`、`sources`。`kind` 为 `page`、`component`、`api`、`function`、`data` 或 `external`。

方法可增加 `symbol`（真实方法名）、`inputs` / `outputs`（每项为 `name`、`type`、中文 `description`）、`steps`（关键步骤字符串列表）、`errors`（异常与边界字符串列表）。未标注的类型写明未知，不凭签名猜测业务语义。字段表同时显示数据字段、方法输入及返回值；点击一行定位对应节点。

例如 `calculate_total()` 的说明应解释数量、单价、优惠和舍入规则，而不只是重复“计算金额”。每个方法使用准确的 `line` 与 `end_line`；文件级引用可解释模块，但不足以判断具体哪个方法被修改。脚本只展示来源哈希匹配、已通过内容校验的有限代码片段。

`description` 用业务语言解释职责，避免只翻译函数名。例如：“这次读取限定订单所属用户，避免把其他人的订单返回给当前用户。”

`provenance`：

- `observed`：agent 已阅读来源并登记依据；必须提供来源。表示分析有来源，不意味着业务已通过运行验证。
- `inferred`：尚未证实的推断，解释推断依据和缺口。
- `unknown`：尚不了解，不补造关系。

来源格式为 `{"path":"src/orders.py","sha256":"完整64位小写文件哈希","line":12,"end_line":25}`。路径为项目内规范相对路径；不允许越界、外部符号链接和 URI。行号可省略，存在时必须有效。文件改变或删除后保留旧引用，并显示过期；只有再次阅读后才能更新来源。

文件哈希可通过 Python `hashlib.sha256(Path(path).read_bytes()).hexdigest()` 获取。文件哈希只能证明所引用的文件未变，不能自动证明中文说明准确。

### 关系 edges

每条关系有稳定 `id`、节点 `from` / `to`、`kind`、`description`、`provenance` 和 `sources`。关系种类是 `calls`、`reads`、`writes`、`navigates`、`contains`、`depends_on`、`references`。

方向始终从依赖者指向被依赖者，例如“页面 → 接口”、“业务函数 → 数据表”。数据表之间使用 `references` 表达字段引用，说明中写清业务含义，例如“每张订单属于一个用户；一个用户可以有多张订单”。

数据节点可增加 `fields` 列表，每个字段包含 `name`、`type`、中文 `description`，可选布尔值 `primary_key`。字段详情继承数据节点的来源与分析状态，不能把未阅读的结构写成已确认事实。

```json
{
  "fields": [
    {"name": "id", "type": "integer", "description": "每张订单的唯一标识", "primary_key": true},
    {"name": "customer_id", "type": "integer", "description": "这张订单属于哪位客户"}
  ]
}
```

`references` 仅允许连接两个数据节点，可附加 `relationship`：

```json
{
  "cardinality": "many-to-one",
  "from_field": "customer_id",
  "to_field": "id",
  "constraint": "foreign-key"
}
```

基数沿 `from → to` 方向解释，可取 `one-to-one`、`one-to-many`、`many-to-one`、`many-to-many`、`unknown`。约束可取 `foreign-key`（数据库外键）、`logical`（业务逻辑关联）、`unknown`。字段映射可在未知时同时省略；提供时必须成对，已登记字段的节点必须能够找到这些字段。

只有 `observed` 且有来源和明确字段映射的关系才能声明数据库外键。agent 必须实际阅读迁移、DDL 或其他权威结构依据；脚本校验引用和证据身份，不解析所有数据库方言来证明约束已部署。仅从字段名字推断的关联只能标为 `inferred` 与 `logical` / `unknown`。多对多关系通常还需要展示中间关联表，不能凭一条示意线推断实际表结构。

图中的箭头表示明确登记的调用、读取、写入或字段引用；这些代码关系不能自动当作用户操作先后。功能全貌中的连线汇总底层代码依赖，进入功能后再展开实际节点。修改前后模型中的关系都会参与影响分析，删除节点和改变调用目标仍保留历史依据。

### 页面原型 screens

页面原型包含 `id`、`feature`、`title`、`description`、`provenance`、`sources`，可选 `fields` 与 `hotspots`。

- `fields`：`{"label":"订单名称","kind":"input"}`；类型可为 `input`、`select`、`text`，只是线框字段。
- `hotspots`：`{"id":"save","label":"保存订单","node_id":"order-page","description":"提交表单并显示保存结果。","scenario_ids":["ORDERS-001"]}`。
- 热点节点必须在该功能的 `node_ids` 中，验收场景必须在该功能的 `scenario_ids` 中。

`screens` 保留为旧模型的补充资料，当前图表页面不渲染原型或热点。通过对应的功能及方法查看职责、来源和验收状态。实际业务操作仍由浏览器验收完成。HTML 无远程依赖，不加载 CDN，也不把未受信任的说明内容当成 HTML 执行。

## 基线、差异与讲解

基线位于 `.project-check/changes/<id>/baseline.json`，同一 ID 不允许覆盖。基线包含源码身份、当时的模型及清单，以及明确映射来源的少量文本。源码文本最多 200 KB/文件、1 MB/基线；凭据类路径、检测到的私钥或令牌、二进制、越界链接和非 UTF-8 文件不复制。源码本身也可能含敏感业务内容，基线和报告应留在项目本地，遵循项目现有保密规则。

源码身份始终包含 `.project-check/project.json`、`inventory.json` 和 `model.json`，即使它们被 Git 忽略。修改验收政策、清单或模型后需要重新建立运行快照；生成的运行、地图和讲解产物不影响身份。目录被符号链接替换时只记录链接身份，不读取链接后的文件。

`review.json` 保存最新比较结果，每次复核另存时间戳历史，包含：

- 从任务基线到当前代码的真实文件差异；不会将所有 Git HEAD 差异算作本次工作。
- 已映射源码的真实 unified diff，最多 600 行 / 64,000 字符。达到限制会明确显示截断。未在基线映射的已有文件不会伪造完整“新增”差异。
- `node_changes`：按完整源码范围比较的方法/节点状态，包含 `before`、`after`、理由、局部代码差异和影响路径。`modified` / `added` / `removed` 表示有对应实际编辑；`affected` 只表示调用或数据路径关联；`unresolved` 表示无法精确判断；`unchanged` 表示登记范围内容一致。
- `graph_before`：冻结的原关系图。修改前后可以切换查看，新增和删除节点保留位置与说明。
- 直接关联节点、反向依赖节点、受影响功能和未映射变化。
- agent 的行为变化、修改原因、学习要点、与计划的差异和未知项。
- `learning_state: unread` 与 `learning_blocks_archive: false`，阅读讲解不会成为技术归档条件。

同文件其他方法未变、仅行号移动、仅修改模型说明，都不能冒充方法源码修改。`metadata_changed` 单独表示说明/参数等模型资料变化。缺少保存文本、完整范围或来源哈希不匹配时，节点保持无法精确判断。调用方可能需要回归；下游数据可能受行为变化影响，但不能据此声称数据库字段被改动。

讲解应围绕用户本次目标，交代原来的行为、现在的行为、修改原因、与预期的差异，以及已经验证和仍待验证的部分。源码差异证明文本变化，行为解释与测试证明分别查看。

讲解文件格式：

```json
{
  "notes": [
    {
      "target_type": "feature",
      "target": "order-save",
      "kind": "behavior",
      "text": "以前保存失败后页面仍显示成功；现在只在服务端确认成功后更新状态，失败时保留输入。"
    },
    {
      "target_type": "node",
      "target": "order-submit",
      "kind": "learning",
      "text": "前端的成功提示取决于服务端结果。这里先等待请求结束，再决定展示成功或错误，避免请求发出就直接显示已保存。"
    }
  ]
}
```

`target_type` 为 `feature` 或 `node`，目标必须存在于修改前或修改后的模型；`kind` 可为 `behavior`、`rationale`、`learning`、`deviation`、`unknown`。脚本强制将这些讲解标成 `agent-authored`、`verified: false`，与真实 diff 和执行证据分开。讲解中的结论不能覆盖需求、测试结果或归档检查。

## 验收与新鲜度

提供 `--run-dir` 时，脚本使用现有汇总器重新读取原生执行证据，并校验冻结清单中的 `workspace.json`。源码或清单变化、源码快照缺失、被修改或未被清单封存时，页面标为“需要重新验证”，保留历史结果，但不给当前功能显示通过。

关系或其依赖链上的来源过期，也会影响功能的分析状态。代码分析状态与验收状态始终分开。尚未分析的文件会出现在“覆盖与未知”中，即使未分析的是文档或配置，也不会被静默算作已覆盖。

离线 HTML 不能监听未来修改。它明确显示生成时间、源码版本和内容身份；重新分析时再运行 `map` 或 `review`。实时自动刷新需要额外触发机制，不能仅依靠 skill 文本保证。
