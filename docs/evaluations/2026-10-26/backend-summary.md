# 创建编辑文章标题最多200字符，超长明确错误且不写入，保留正常行为并讲解验收

验收结果：**已通过**

## 改了什么

- create：原来「为当前登录用户保存新文章；现在仅拒绝空标题，非空标题没有长度限制。」；现在「为当前登录用户创建文章；拒绝空标题或超过200字符的标题，显示明确错误；校验通过后才写入并跳转列表。」。原因：用户要求标题最多200字符；在INSERT之前增加服务端长度校验，浏览器或直接POST均无法绕过，沿用既有flash错误渲染。
- update：原来「只允许作者编辑文章；现在仅拒绝空标题，非空超长标题仍会覆盖原记录。」；现在「作者编辑自己的文章；拒绝空标题或超过200字符的标题；失败时标题和正文均不覆盖原记录，通过后提交更新。」。原因：编辑也必须遵守同一限制；在UPDATE之前拒绝超长标题，保留原有标题与正文，且继续先由get_post检查文章与作者权限。

## 影响哪里

涉及功能：创建与编辑文章
- 保存文章标题、正文及作者引用；本次不改变数据库结构。（post 表）
原来非空标题没有长度上限；现在创建和编辑都只接受最多200字符，超长输入给出明确错误，且数据库完全不写入。200个中文或单码点emoji可正常保存。

## 验收了什么

- 创建编辑接受 1/199/200 字符及 Unicode 边界：已通过
- 创建编辑拒绝超长标题且不写入：已通过
- 原有完整 pytest 回归：已通过
- 浏览器超长创建拒绝及 200 字符保存刷新：已通过
- 浏览器超长编辑拒绝及 200 字符保存刷新：已通过

## 还有哪里没验证

- 浏览器与界面边界：本轮真实交互仅使用桌面Chrome、1280×800视口；未执行Firefox、WebKit或移动端视口流程。
- 标题字符边界：断言覆盖ASCII、中文及单码点emoji；未执行组合音标和ZWJ复合emoji的边界用例。计数口径仍沿用已登记需求的Unicode码点，没有新增按视觉字形计数的要求。
- 环境与CLI边界：本轮持久化在本地Flask和隔离SQLite中验证；未做生产部署验收。既有init-db命令测试用monkeypatch核对分发，未把该用例视为真实CLI初始化数据库的端到端验证。
- 分析/讲解缺口（不等同于未执行测试）：以下改动文件尚未映射到功能：.project-check/assessment.json、.project-check/inventory.json、.project-check/model.json、.project-check/project.json、playwright.config.ts、tests/browser/phase-errors.ts、tests/browser/phase-rule-engine.d.mts、tests/browser/phase-rule-engine.mjs、tests/browser/project-check-reporter.ts、tests/browser/server.py、tests/browser/title.spec.ts、tests/test_title_length.py

## 与原计划的差异

- 原本新增测试误设HTTP302；读取固定Flask上游源码确认默认303，已纠正为303并保留首轮失败。Playwright浏览器下载长时间停在0%，有界终止后使用本机Chrome独立临时配置。

版本：48de43957e6486596a4d4c14de634ee539352764；运行：20261026T072304Z-10603376

[打开本次任务详情](/private/tmp/project-check-flask-summary-fix.AQeRio/tutorial/.project-check/views/project.html)
