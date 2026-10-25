# 给 Agent 的一次安装说明

用户可以直接说：

> 请按 https://github.com/timmyagentic/agentic-fan/blob/codex/apple-silicon-dmg/INSTALL.md 安装最新正式版 AgenticFan App、CLI 和当前 Agent 的 skill，保留已有配置，最后只读检查真实温度和风扇接口，并报告版本、安装路径与未完成项。

这句话已授权正常的本地安装步骤。无需让用户重复确认下载、安装 App、添加 CLI 和 skill。根据当前会话选择 Codex 或 Claude Code；用户要求时也可以同时安装两种 skill。

## 安装流程

1. 确认执行机器是 **Apple Silicon、macOS 15+**。Linux 可以开发测试，不能把 Linux 上的模拟输出当作 Mac 安装成功。
2. 检查当前是否已有 `agenticfan`。安装目标为 `~/Applications/AgenticFan.app`、`~/.local/bin/agenticfan`；Codex skill 位于 `~/.agents/skills/agenticfan`，Claude Code skill 位于 `~/.claude/skills/agenticfan`。
3. 正式安装无需 GitHub 登录。匿名下载 [最新正式 Release](https://github.com/timmyagentic/agentic-fan/releases/latest) 中的安装脚本，阅读脚本后执行；不要覆盖用户已有的源码目录：

```sh
agenticfan_setup="$(mktemp -d "${TMPDIR:-/tmp}/agenticfan-setup.XXXXXXXX")"
curl --fail --location --proto '=https' \
  https://github.com/timmyagentic/agentic-fan/releases/latest/download/install-macos.sh \
  --output "$agenticfan_setup/install-macos.sh"
# 阅读下载的脚本后执行：
bash "$agenticfan_setup/install-macos.sh" --agent codex
```

4. 从当前 Agent 的会话上下文选择目标：Codex 用 `--agent codex`，Claude Code 用 `--agent claude`；用户要求两者时用 `--agent both`。上下文已明确时不要重复询问。安装不需要 `sudo`、Python、Xcode 或本地编译。
5. 安装器只解析一次正式 Release 的 tag，随后固定同一版本下载 DMG 和 SHA-256。核对校验和、磁盘镜像、代码签名与 Gatekeeper 后安装 App、CLI 和 skill。它保留 shell 配置并添加 PATH；重复安装不会追加配置。自定义 CLI/skill 冲突会被保留并报错，旧 App 保留为同目录下的 `AgenticFan.previous.*.app`。
6. 当前 Agent 进程的 PATH 不会随父 shell 配置自动更新。立即使用以下绝对路径验证；新登录 shell 可以直接使用 `agenticfan`：

```sh
"$HOME/.local/bin/agenticfan" schema
"$HOME/.local/bin/agenticfan" doctor
"$HOME/.local/bin/agenticfan" snapshot
```

安装后的新 Agent 会话可以发现 `agenticfan` skill；当前会话可直接读取对应 `SKILL.md` 并使用 CLI。不要为了安装擅自重启用户正在使用的 Agent。

首次正常打开已安装的 AgenticFan App 时，默认开启当前用户的登录自启。登录后只驻留菜单栏，不显示 Dock 图标，也不弹出或恢复主窗口。菜单栏的“打开主窗口”提供完整界面；应用设置中的“登录时自动启动”和 macOS 登录项设置均可关闭自启，后续启动会保留选择。只运行 CLI 或隔离安装测试不会注册登录项。

App 启动时也会载入用户已有的温度规则；默认规则关闭。若先前已明确启用规则且 Helper 就绪，登录启动后也会按这些条件控扇。可先用 `agenticfan rules show` 查看配置，或用 `agenticfan rules disable --wait 8` 关闭并检查恢复回执。登录项开关与温度规则开关分别管理。

Agent 可通过原生入口直接管理登录项，无需打开窗口。`status` 只读；`enable` / `disable` 对当前用户执行显式开关，返回 JSON 中的系统 `state`、`enabled` 和 `error`。这不会启动监测进程或修改风扇规则：

```sh
"$HOME/Applications/AgenticFan.app/Contents/MacOS/AgenticFanUI" --login-item status
"$HOME/Applications/AgenticFan.app/Contents/MacOS/AgenticFanUI" --login-item enable
"$HOME/Applications/AgenticFan.app/Contents/MacOS/AgenticFanUI" --login-item disable
```

## 已下载的安装包

已有 DMG 和同名 `.dmg.sha256` 文件时，可以离线安装，无需 GitHub CLI。正式 Release 与 Actions 下载包的根目录均附带 `install-macos.sh`；源码仓库中的脚本位于 `scripts/`：

```sh
bash /absolute/path/install-macos.sh --dmg /absolute/path/AgenticFan-0.4.2-arm64.dmg --agent both
```

需要开发构建时显式传 `--run-id ID`，这一路径才需要已登录的 GitHub CLI。安装器只接受本仓库成功的 push 或手动 DMG 构建。开发包带 `-dev` 后缀且未经 Apple 公证，和默认正式安装路径分开。

## 验收与权限

报告三个独立结果：App/CLI 是否安装完成、skill 是否已放置、实际温度和风扇接口是否可用。`doctor` 退出码为 0 只代表诊断成功返回；必须检查 `data.status`、`temperature_interface_available`、`monitoring_available`、`data_quality`、`sensor_summary`、`control_interface_available`、`warnings`、`errors` 与 `simulated`。无风扇设备可以温度监控成功而没有风扇控制接口。`valid_sensor_count` 只计通过合理性检查的读数，不代表已验证的物理传感器数量。

升级也按相同安装流程执行，以同步更新 App、CLI 与所选 Agent 的 skill；若 skill 被用户修改，保留冲突并报告。0.2.0 将 JSON 协议升为 v2：零值和异常值的 `value` 均为 `null`，原始数值存于 `raw_value`，不得用作可用温度。升级后重新读取 `schema`，确认 `--version`，并执行只读 `doctor`、短期 `watch` 复查；规则详见 [温度有效性说明](docs/temperature-validation.md)。

安装过程不做真实风扇写入，不配置免密 sudo，也不把模拟结果当作硬件验收。用户要求启用控扇时，以普通用户运行 `agenticfan helper install`，通过系统授权安装 root 持有的 Helper。之后的 `fans set` / `fans auto` 无需 sudo 或重复认证。GUI 首次控扇会执行同一步骤。`helper status` 检查版本与服务；`helper uninstall` 经管理员授权卸载。活动会话期间禁止安装/卸载。

正式 Release 使用 Developer ID 签名和 Apple 公证；默认下载安装遇到 Gatekeeper 验证失败会停止，不安装该包。脚本保留系统安全设置和 quarantine。显式选择开发包时可能仍需用户通过 macOS 系统界面确认打开。安装与监控不应要求管理员密码；首次安装或更新 Helper 需要管理员授权，日常控扇由后台服务执行。

用户取消系统授权、仓库不可访问或硬件接口不可用时，保留具体错误并报告已完成的部分，不把任务笼统标为成功。
