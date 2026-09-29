# 配置一次，以后在 GitHub 点击发布

发布入口：[Actions → Publish npm package](https://github.com/Juxn-Leung/project-check/actions/workflows/publish.yml)。

## 首次接入（只做一次）

### 1. 首次发布 npm 包

Trusted Publisher 需要先有一个属于你的 npm 包。当前包名为 `project-check-skill`，首版为 `0.1.0`。

在本仓库根目录执行以下命令，并按 npm 提示完成登录和二次验证。不要将密码或令牌提交到仓库。

```sh
npm login --registry=https://registry.npmjs.org
npm publish --access public
```

本仓库的 `prepublishOnly` 会先执行 JavaScript 与 Python 测试。若发布命令中断，先检查 `npm view project-check-skill@0.1.0 version`，确认是否已成功，避免无意义地重复升版本。

### 2. 在 npm 设置可信发布

打开 [npm 包设置](https://www.npmjs.com/package/project-check-skill/access)，进入 **Settings → Trusted publishing / Trusted Publisher**，选择 **GitHub Actions** 并填写：

| 字段 | 值 |
| --- | --- |
| Organization or user | `Juxn-Leung` |
| Repository | `project-check` |
| Workflow filename | `publish.yml` |
| Environment name | 留空（工作流没有配置 GitHub Environment） |
| Allowed actions | 启用直接发布 `npm publish` |

工作流名只填 `publish.yml`，不要填完整路径。若只启用了 `npm stage publish`，会需要额外人工批准，不符合本项目的直接发布流程。

无需设置 `NPM_TOKEN`、`NODE_AUTH_TOKEN` 或其他长期 npm Secret。GitHub 托管 runner 在每次发布时通过 OIDC 获取短期身份。

若仓库禁用 GitHub Actions，先启用它。工作流会请求 `contents: write` 以同步版本和标签；如果组织策略或 master 分支保护拒绝 bot 推送，需要由仓库管理员选择合适的发布分支策略，不要为了发布自动绕过保护。

### 3. 做一次预演和一次真实验证

打开发布入口 → **Run workflow** → 分支选择 **master** → 版本类型选择 **patch** → 勾选 **dry_run** → 运行。

预演会升级临时工作区版本、运行测试、打包并通过 npm 安装真实 tarball；不会发布或推送。产物可在运行页面的 Artifacts 下载。**预演不能验证 npm OIDC 授权**；保存可信发布配置后，需要第一次真实发布确认链路。

## 以后每次发布

1. 将准备发布的改动提交并推送到 `master`。
2. 打开 **Actions → Publish npm package → Run workflow**。
3. 保持分支 `master`，选择版本类型，保持 `dry_run` 未勾选，点击运行。

| 类型 | 示例 | 用途 |
| --- | --- | --- |
| `patch`（默认） | `0.1.0 → 0.1.1` | 修正说明、缺陷修复、小改动 |
| `minor` | `0.1.1 → 0.2.0` | 新增兼容功能 |
| `major` | `0.2.0 → 1.0.0` | 不兼容变更 |
| `current` | 保持当前版本 | 已手动准备版本且尚未发布等特殊情况 |

工作流依次验证、打包、安装验证、检查版本占用、发布、核对注册表 tarball 完整性，最后把版本提交和 `vX.Y.Z` 标签原子推送到 `master`。使用固定来源提交计算版本，重跑同一个未完成的运行不会因为 npm 已存在该版本而自动再升一次版本。

成功后，在本地仓库执行 `git pull --ff-only`，同步自动生成的版本提交，再继续开发。

## 失败与重试

- **测试或打包失败**：不会发布。修复代码并推送，然后重新运行。
- **npm 授权失败**：确认包已经存在、Trusted Publisher 的三个身份字段正确，并允许直接 `npm publish`。不需要添加 token 兜底。
- **同版本不同内容**：工作流拒绝覆盖；发布新版本。同版本内容相同则跳过重复上传，继续核验和同步。
- **npm 成功但 Git 同步失败**：运行摘要会明确说明，不会撤回 npm 包或强推 Git。优先对同一个运行执行 **Re-run failed jobs**；若 master 已有新提交，则需要核对 npm 已发布版本并把相应版本提交/标签补齐，再发下一版。
- **启动后 master 已变化**：拒绝使用过时来源发布，请从最新 master 启动新运行。
- **预演成功、真实发布失败**：预演验证了代码与安装，未验证 npm 账户权限；查看真实运行的错误。

每次运行都会尽可能保留实际测试过的包和 `release-plan.json`，便于判断某个版本是否已经发布。不同版本发布串行执行，且不会取消正在发布的任务。

## 本地验证发布脚本

在干净的临时 checkout 中运行：

```sh
node scripts/release.mjs patch true
```

本地只能预演。正式发布入口要求 GitHub Actions 的 `master` 上下文。独立脚本不包含在 npm 安装包内；使用者安装 Skill 不会获取发布权限。

参考：[npm Trusted Publishing](https://docs.npmjs.com/trusted-publishers/)、[GitHub 手动运行工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。
