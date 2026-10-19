# Mac 应用与 DMG 构建

交付物为 `MacFan.app`，放在真正的 UDZO/HFS+ DMG 中，附带指向 `/Applications` 的拖放安装链接。应用携带 Python、Tk 和全部 Python 模块；终端、Agent 与 GUI 都使用 `Contents/MacOS/macfan`。不要求用户安装 Python 或 pip。

MacFan 常驻菜单栏，打开主窗口、关闭窗口和收起菜单栏弹窗时都不显示 Dock 图标，也不占用 Command-Tab 应用切换器。菜单栏保留“打开主窗口”和“退出”入口；主窗口关闭后继续监测。打包声明 `LSUIElement=true`，原生入口也使用 accessory 激活策略，菜单栏弹窗不会再将应用切回普通 Dock 应用。

已安装的 App 首次正常启动时，默认通过 `SMAppService.mainApp` 注册当前用户的登录项。以后登录时自动启动，只在菜单栏显示，不自动打开或恢复上次的主窗口；需要时由菜单栏按需打开。应用设置可关闭“登录时自动启动”，系统设置中关闭的选择也会保留，后续启动不会重新开启。注册失败会在应用设置显示错误；系统要求允许时，提供登录项设置入口。

自启只启动原生 App，并使用同一监测核心；不改变风扇规则、Helper 权限或实际风扇设置。构建、安装验收中的 `--demo` / smoke 启动完全跳过登录项与首次配置标记，隔离安装也不会注册构建机用户的登录项。

## 在 Apple Silicon Mac 构建

需要 macOS 15+、带 Tk 的 arm64 Python 3.12、Xcode Command Line Tools 和可用的桌面会话。不要使用 Rosetta 下的 x86_64 Python。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r packaging/macos/requirements.txt
.venv/bin/python -m unittest discover -v
.venv/bin/python packaging/macos/build.py --output dist/macos
```

默认产物：

- `MacFan-0.3.0-arm64-dev.dmg`：ad-hoc 签名、未经公证的开发包。
- 同名 `.dmg.sha256`：完整 DMG 的 SHA-256。
- 同名前缀 `.build.json`：工具版本、架构、签名、公证状态与完成的打包检查。
- `install-macos.sh` 和 `INSTALL.md`：Agent 安装脚本和安装说明；可用本地 DMG 离线安装。

构建器拒绝 Linux、Intel 和已存在的目标 DMG。重新构建请指定新的输出目录。构建产物受 `.gitignore` 排除，不应提交到源码仓库。

## 构建检查

构建器会检查应用签名，并从源码目录之外运行内置 CLI 的 `schema`、模拟快照和有限控扇会话；后者必须返回自动模式恢复确认。随后启动窗口演示，检查传感器与风扇表格加载成功。生成 DMG 后，再验证、只读挂载磁盘镜像，核对应用与安装链接，并在挂载卷中重跑 CLI 和窗口检查。

原生启动 smoke 另外检查：冷启动没有主窗口、显式请求后出现主窗口、关闭后可以再次打开、全过程保持 accessory 策略。模拟检查不验证真实登录项注册；真实登录后启动仍需在目标用户会话中验收。

所有这些功能检查只操作显式模拟器，不改写构建机器的风扇。真实硬件读写仍需 [独立验收](hardware-validation.md)。直接启动内置可执行文件的检查也不能替代从 Finder 双击、管理员授权对话框、拖放安装和 Gatekeeper 的人工验收。

## GitHub Actions

`.github/workflows/macos-dmg.yml` 使用 GitHub 的 `macos-15` arm64 runner。相关代码推送或 PR 可触发构建，也支持手动运行。成功后在该次 Actions 的 **Artifacts** 中下载 `MacFan-Apple-Silicon-development-DMG`，解压得到 DMG 和校验信息。

工作流只上传构建产物，不创建 Release、不自动发布软件，也不需要签名凭证。首个版本已通过 [macOS 构建与镜像检查](https://github.com/timmyagentic/agentic-mac-fans-control/actions/runs/38715565877)；后续版本以对应提交的 Actions 日志为准。

构建还会在临时用户目录中运行 `scripts/install-macos.sh`，检查 App 安装、CLI 软链接、Codex/Claude Code skill、shell 配置保留、重复安装及校验失败时的行为。独立的 `install-smoke.yml` 工作流可固定一个成功的构建 ID，验证从 GitHub 下载再安装的完整流程。这些检查不安装到 CI 用户的真实目录，也不调整真实风扇。

## 正式签名与公证

在受信任的 Mac 构建机钥匙串中安装自己的 **Developer ID Application** 证书，并通过 Apple `notarytool store-credentials` 保存公证凭证。不要把证书私钥或口令写入仓库。

```sh
.venv/bin/python packaging/macos/build.py \
  --identity 'Developer ID Application: Your Name (TEAMID)' \
  --notary-profile 'macfan-notary' \
  --output dist/release
```

此模式启用 hardened runtime，签名应用内部二进制和外层 bundle，分别提交应用与 DMG 公证并装订票据；成功后输出不含 `-dev` 的 DMG。只传 `--identity` 而没有公证配置时，产物名带 `-signed-unnotarized`。当前会话未执行正式签名或公证。

开发包可能被 Gatekeeper 拦截，不应被描述为已经具备正式分发体验。应在一台未参与构建的 Mac 上验收下载、安装、授权、Agent 调用和卸载流程。
