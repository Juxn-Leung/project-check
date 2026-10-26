# 确认后清空所有待办，刷新仍为空，原有待办功能继续可用

验收结果：**已通过**

## 改了什么

- Controller.constructor：原来「将新增、编辑、完成、删除、清除已完成和全选事件绑定到控制器。」；现在「将原有待办事件和新的 removeAll 回调绑定到对应控制器方法。」。原因：使新按钮进入既有 MVC 调用链
- Store.constructor：原来「在内存对象中初始化待办集合，刷新后不保留。」；现在「初始化 localStorage 中的待办 JSON 集合，读取已保存的最大 ID，供刷新后安全新增。」。原因：让跨刷新清空可被有效验证，且不使刷新后新增误用旧 ID
- View.constructor：原来「缓存页面控件并绑定 render/bindCallback 的 this。」；现在「缓存原有控件和新增 Clear all 按钮。」。原因：注册新入口需要稳定 DOM 引用
- View.bindCallback：原来「将 DOM 事件及待办 ID/内容转换为控制器回调。」；现在「原有事件继续处理；Clear all 点击先用原生 confirm 询问，确认才调用控制器，取消不写数据。」。原因：删除不可逆，用户要求删除前确认
- src/index.html：原来「原始待办入口，含输入框、列表、筛选、Clear completed，没有清空全部入口。」；现在「待办入口新增 Clear all 按钮；沿用无待办时隐藏底栏的行为。」。原因：允许清空未完成及已完成项
- src/app.css：原来「示例的局部附加样式，尚无清空全部按钮样式。」；现在「把 Clear all 放在底栏独立一行，并提高其层级，避免底栏装饰伪元素拦截点击。」。原因：避免和原有筛选、计数、Clear completed 控件重叠；第一轮真实点击显示 footer 伪元素拦截按钮；将 Clear all 设为 position:relative / z-index:1 后，使按钮位于装饰层之上。旧失败 run 01-implemented 保留，测试未使用 force click。
- memoryStorage：原来「本模块内的 JSON 字符串集合；未跨刷新持久化。」；现在「保留既有 Store JSON 接口，通过 localStorage 持久保存 id/title/completed；memoryStorage 是保留的上游变量名。」。原因：满足上游 Persistence 规范；清空只写本应用命名集合
- Controller.removeAllItems：原来「无已保存说明」；现在「通过已有模型清空整个集合，并强制按当前筛选重绘；不会仅删除可见待办。」。原因：复用全量存储清空，避免只删筛选中可见记录

## 影响哪里

涉及功能：清空全部待办、原有待办交互
- 当前由 Controller.removeAllItems 在用户确认后调用，委托 Store.drop 清空本应用的全部待办；完成后通过回调触发当前筛选和计数刷新。（Model.removeAll）
- 将本应用 localStorage 命名集合中的 todos 写为空数组，再把空数组传给回调；不删除其他 localStorage 集合。（Store.drop）
- 在本应用 localStorage 集合中新增待办或按 ID 更新标题、完成状态；新 ID 由初始化时恢复起点后的计数器分配，避免刷新后误用已有 ID。（Store.save）

## 验收了什么

- 确认删除所有待办并保持清空：已通过
- 取消确认保留全部待办：已通过
- Active 筛选下清空隐藏的已完成项：已通过
- Completed 筛选下清空隐藏的未完成项：已通过
- 新增编辑单条删除及刷新后 ID 唯一：已通过
- 筛选批量完成及只清除已完成：已通过

## 还有哪里没验证

- 仅在本机 Chromium 1280x800 验收；未测试其他浏览器、跨标签页同步、localStorage 禁用或损坏等情况；未声称整项目完整验收。
- 分析/讲解缺口（不等同于未执行测试）：以下改动文件尚未映射到功能：.project-check/inventory.json、.project-check/model.json、.project-check/project.json、dist/app.bundle.js、dist/app.bundle.js.map、dist/app.css、dist/app.css.map、dist/index.html、playwright.config.ts、tests/todos.spec.ts

## 与原计划的差异

- 上游目录已由 vanilla-es6 改名为 javascript-es6。当前内存实现偏离上游持久化规范，本次改用 localStorage。首次试跑发现 Playwright 的规范化 ID 相对 testDir，不带 tests/；按真实适配输出修正了映射，保留旧 run。

版本：69580e2e4b5b31aa61b07d2b8b3f4e311a20a5a2；运行：20261026T073107Z-59588432

[打开本次任务详情](/private/tmp/project-check-final-web.mOaj2X/app/.project-check/views/project.html)
