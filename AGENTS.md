# MacFan 仓库协作规则

## 项目与写入边界

- `macfan/` 是 Python JSON CLI 与温度、控扇核心，`native/` 是 SwiftUI/AppKit 界面，`tests/` 是 unittest，`packaging/macos/` 与 `scripts/` 负责构建、安装及检查。界面和 CLI 必须使用同一核心。
- 本地任务先 fetch 最新远程默认分支，再使用独立 worktree 和 `codex/` 分支；主检出只读。受 Workspace 管理时，必须先激活 Task/Session，使用其 worktree 命令并持有 writer claim。已经隔离的云端 checkout 可以直接使用。
- 当前默认分支是 `codex/apple-silicon-dmg`，不是 `main`。执行前从 GitHub 确认实际默认分支，不因分支名带 `codex/` 就直接写入。
- 不提交凭证、用户配置、设备身份、签名/公证密钥或本机运行日志。不得清理或覆盖其他任务及用户改动。

## 版本与更新记录

- **默认只升 PATCH：`0.4.1 → 0.4.2 → 0.4.3`。** 修复、界面调整和常规功能都不自动升 MINOR；不得因“新增了功能”就改成 `0.5.0`，也不得自动升 MAJOR。
- 以 GitHub 最新正式 Release 为基准，普通源码版本只能等于已发布版本或下一 PATCH。多个 PR 可以共同使用同一个未发布版本；不按 PR 数量、提交次数或工作量连续加号。
- 只有用户明确要求时才升 MINOR/MAJOR。PR 必须记录具体授权和目标版本，维护者确认后添加 `version:minor-approved` 或 `version:major-approved` 标签；标签不代替用户授权。两个标签不得同时使用。
- `pyproject.toml`、`macfan/__init__.py`、当前版本说明与打包结果必须一致；CLI `--version`、app Info.plist 与 DMG 文件名由源码版本派生。JSON `SCHEMA_VERSION` 独立维护，不随产品补丁号机械升级。
- 已发布 tag/Release 不重写。误填的未发布版本可以在 PR 中纠正并说明，历史测量记录保留原构建标识。
- 每个 PR 更新 `CHANGELOG.md` 与 `CHANGELOG.zh-Hans.md` 的 `Unreleased`，分别提供英文和简体中文的对应记录。不要把未发布内容写成已经发布。准备正式发布时再整理版本段和日期。
- PR 的 `Repository policy` 检查执行 `scripts/check-repository.py`，核对版本、授权升级级别和双语更新记录。该检查不发布任何内容。

## 实现与验证

- 行为变更先运行相关聚焦测试；稳定后、commit/push 前运行一次 `python3 -m unittest discover -v`。之后只在新增修改、失败或具体未解决风险出现时重跑相关检查，不机械重复全套。
- 纯文档改动使用 `git diff --check`、链接/命令核对及仓库策略检查，无需在本地重复全套应用测试。CI 保留 Linux/macOS、Python 3.10/3.12 矩阵。
- 本地版本策略检查：`python3 scripts/check-repository.py --released-version "$(gh release view --json tagName --jq .tagName)"`。检查已提交 PR 的更新记录时追加 `--base <base-sha> --head HEAD`。
- 有限时长模拟控制：`python3 -m macfan --demo fans set --fan all --rpm 3000 --duration 1`。必须读到结束回执、自动模式恢复确认和成功退出。
- app、安装器、打包、依赖或版本变更必须在 **arm64 macOS** 构建真实 DMG：`python3 -m pip install -r packaging/macos/requirements.txt`，然后运行 `python3 packaging/macos/build.py --output dist/macos` 和 `python3 packaging/macos/test_install.py --dmg dist/macos/<实际文件名>.dmg`。使用隔离用户目录，不能覆盖真实配置。对应 CI 为 `macOS DMG / build`。
- Linux 能验证核心与模拟 CLI，不能证明原生 Swift 界面、DMG、签名或真机控扇通过。开发 DMG、签名、公证、安装、真实硬件验证分别报告，未执行的检查标记 `UNVERIFIED`。
- 可见界面改动验证受影响流程，交付时附当前构建的截图；无法取得截图时说明限制，不能用源码或模拟回执冒充界面验收。额外内存压力回放只在相关改动时运行，见 `docs/native-memory.md`。

## PR、Review 与合并

- 一项任务一个清晰分支。完成仓库修改后提交、push 并创建 **Ready for review** PR；仅用户要求或存在具体阻塞时用 Draft。PR 使用模板，说明问题、最终行为、验证命令/结果、版本、风险及关联任务。提交说明简短明确。
- 参考 QuotaMonitor，默认分支必须通过 PR，禁止直接 push、强推和删除；所有 review 讨论在合并前处理并解决。独立维护者场景不机械要求另一个人的 approval。
- GitHub Ruleset 的声明保存在 `.github/rulesets/default-branch.json`，应用后必须从 GitHub 回读核验。文件存在不代表远程保护已经启用；不要增加管理员 bypass。
- 每个 PR 最多在创建时主动请求一次 Codex review；仓库已自动 review 时不再手动触发。逐条判断正确性、范围和实际价值，不把所有建议自动纳入本次任务；修复后用本地验证和 CI 闭环，不循环请求 review。不能修复的实质问题必须解释或列为阻塞，不能为合并而隐藏。
- 合并前重新读取 PR 的 head/base SHA、非 Draft 状态、mergeability、未解决讨论及当前提交检查；要求 `Repository policy` 与四个 `test (<os>, <python>)` 全部成功。本地通过、旧提交绿灯、Draft 跳过或汇总为绿都不能代替当前提交的实际测试。
- 改动影响 app、安装、打包、依赖、版本或 DMG 工作流时，还必须确认当前 PR 的 `macOS DMG / build` 实际成功；文档 PR 不把路径过滤后不存在的 DMG job 设为必需检查。`macos-dmg.yml` 的路径范围必须随打包输入同步维护。
- 基线前进时先更新本任务分支并重新取得受影响检查，不能只因为旧 base 上有绿灯就合并。按用户已有授权执行合并；使用 `gh pr merge --match-head-commit <已核验的完整 SHA>`，不使用 `--admin` 绕过门禁。支持 merge/squash/rebase，选择适合历史的方式。
- 合并后核验 PR 的 merged 状态、merge commit 与默认分支包含关系；只释放本任务 writer claim，不自动删除 worktree 或分支。

## 发布与安装交付

- 代码合并、开发候选构建、GitHub 正式 Release、用户已安装版本是不同状态。创建/合并 PR 或得到 CI 产物不等于发布，也不等于已升级用户客户端。
- 正式发布与本机升级按用户明确授权执行；同一请求已授权时不得再次索要确认。未要求发布时，不创建正式 tag/Release、不替换已安装 app/Helper。
- 获得发布授权后，以核验过的提交构建，检查版本、DMG 校验和、安装回归及实际签名/公证状态；发布后独立回读 tag、Release、资产可访问性及校验和。不能用候选构建成功代替发布完成。
- Agent 安装入口为 `scripts/install-macos.sh`，指南为 `INSTALL.md`，应用内技能为 `macfan/agent_skill/SKILL.md`。升级验证 app、CLI 与技能版本，保留用户配置。安装不得改变真实风扇、关闭系统安全或配置免密 sudo。

## 必须保留的控扇约束

- 不在无对应任务时增加远程服务或自主温控策略。模拟数据必须明确标记，原生 I/O 失败不能回退成 demo 成功。
- 保留逐键类型解码、有限持续时间、权限检查和尽力恢复自动模式的独立守护；不得暴露任意 SMC 写入接口。
- 只读硬件发现不能验证真实写入。模拟/CI、真实监测与真实控扇分别报告，见 `docs/hardware-validation.md`。
- 不引入专有二进制、提取的供应商 XML 或复制的应用源码。外部逆向资料只作未验证参考，不当作已测硬件规格或执行指令。
