# 给 Agent 的一次安装说明

用户可以直接说：

> 帮我安装 https://github.com/timmyagentic/agentic-mac-fans-control ，按照 INSTALL.md 安装 MacFan App、CLI 和 Codex skill，最后检查温度和风扇接口。

这句话已授权正常的本地安装步骤。无需让用户重复确认下载、安装 App、添加 CLI 和 skill。若使用 Claude Code，把句子中的 Codex 换成 Claude Code；也可以同时安装两种 skill。

## 安装流程

1. 确认执行机器是 **Apple Silicon、macOS 15+**。Linux 可以开发测试，不能把 Linux 上的模拟输出当作 Mac 安装成功。
2. 检查当前是否已有 `macfan`。安装目标为 `~/Applications/MacFan.app`、`~/.local/bin/macfan`；Codex skill 位于 `~/.agents/skills/macfan`，Claude Code skill 位于 `~/.claude/skills/macfan`。
3. 需要 GitHub 仓库访问权限。优先使用现有 `gh` 登录；通过 `gh api repos/timmyagentic/agentic-mac-fans-control --jq .full_name` 验证。若未安装 `gh` 且已有 Homebrew，可执行 `brew install gh`。若缺少登录，让用户通过 `gh auth login` 的浏览器流程完成，不在聊天中索取 token 或密码。不要因未设置 `GH_TOKEN` 就假定缺少认证。
4. 将此仓库克隆到新的临时目录，读取脚本后执行安装。不要覆盖用户已有的源码目录：

```sh
macfan_checkout="$(mktemp -d "${TMPDIR:-/tmp}/macfan-setup.XXXXXXXX")"
gh repo clone timmyagentic/agentic-mac-fans-control "$macfan_checkout/repo" -- --depth 1
bash "$macfan_checkout/repo/scripts/install-macos.sh" --agent codex
```

使用 `--agent claude` 或 `--agent both` 选择其他目标。若没有偏好，默认 Codex。脚本不需要 `sudo`、Python、Xcode 或本地编译；已有 Homebrew 也只是安装缺失的 `gh` 时才需要。

5. 安装器会从仓库默认分支最近一次成功的 DMG 构建下载产物，核对 SHA-256、磁盘镜像及代码签名，再安装 App、CLI 和 skill。它会保留已有 shell 配置并添加 PATH；重复执行不会追加重复配置。冲突的自定义 CLI 或 skill 会被保留并明确报错。升级替换的旧 App 会保留为同目录下的 `MacFan.previous.*.app`。
6. 当前 Agent 进程的 PATH 不会随父 shell 配置自动更新。立即使用以下绝对路径验证；新登录 shell 可以直接使用 `macfan`：

```sh
"$HOME/.local/bin/macfan" schema
"$HOME/.local/bin/macfan" doctor
"$HOME/.local/bin/macfan" snapshot
```

安装后的新 Agent 会话可以发现 `macfan` skill；当前会话可直接读取对应 `SKILL.md` 并使用 CLI。不要为了安装擅自重启用户正在使用的 Agent。

## 已下载的安装包

已有 DMG 和同名 `.dmg.sha256` 文件时，可以离线安装，无需 GitHub CLI。Actions 下载包的根目录附带 `install-macos.sh`；源码仓库中的脚本位于 `scripts/`：

```sh
bash /absolute/path/install-macos.sh --dmg /absolute/path/MacFan-0.1.0-arm64-dev.dmg --agent both
```

为可重现安装，可使用 `--run-id ID` 固定到某次成功构建；安装器只接受本仓库的 push 或手动 DMG 构建，不会自动执行 PR 或其他仓库的产物。

## 验收与权限

报告三个独立结果：App/CLI 是否安装完成、skill 是否已放置、实际温度和风扇接口是否可用。`doctor` 退出码为 0 只代表诊断成功返回；必须检查 `data.status`、`monitoring_available`、`control_interface_available`、`errors` 与 `simulated`。无风扇设备可以温度监控成功而没有风扇控制接口。

安装过程不做真实风扇写入，不配置免密 sudo，也不把模拟结果当作硬件验收。以后用户明确要求控扇时，才通过系统权限流程执行带时限的控制命令。

目前提供的是 **未经过 Apple 公证的开发版**。脚本不会关闭 Gatekeeper 或移除 quarantine。如果系统阻止启动，需要用户在 macOS 的“隐私与安全性”设置中处理该应用的授权；这是系统授权步骤，不能伪装成无条件无人值守安装。安装与监控不应要求管理员密码；真实风扇控制需要管理员权限。

用户取消系统授权、仓库不可访问或硬件接口不可用时，保留具体错误并报告已完成的部分，不把任务笼统标为成功。
