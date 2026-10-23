# AgenticFan — Fan control for Mac. Built for agents.

[项目官网](https://agenticfan.timmyagentic.si) · [网站源码与部署](website/README.md)

提供温度监测、有限时长调速与可由 Agent 编辑的温度规则。面向 Apple Silicon，交付格式为 **DMG 内的 `AgenticFan.app`**，内含窗口、CLI 和所需运行时。用户不需要安装 Python，也不需要模型、API Key 或网络服务。

**0.4.2 更新：修复原生界面持续监测时的内存增长。** 每轮读取后释放 JSON 临时对象；界面忙时合并待显示的采样，保留控制、错误与恢复回执的顺序。新增保持后台进程存活的 255 测点回放与物理内存回归测试，避免退出进程掩盖泄漏。详见[原生界面内存检查](docs/native-memory.md)和[更新记录](CHANGELOG.zh-Hans.md)。普通更新只升补丁号，协作与合并规则见 [AGENTS.md](AGENTS.md)。

**0.4.2 更新：增加 Agent 可直接编辑的本地温度规则。** 预置 CPU ≥99°C 满速、≤95°C 持续 5 秒恢复自动，默认关闭；同一份 JSON 支持多条规则、最高/固定 RPM、热加载与版本冲突检查，CLI 分别反馈保存、载入、触发和转速确认。详见[温度规则与 Agent 配置](docs/temperature-rules.md)。

**菜单栏与登录启动。** 应用只在菜单栏驻留，不显示 Dock 图标；首次正常打开默认注册当前用户的登录项，可在设置关闭。启动时不弹出主窗口，通过菜单栏“打开主窗口”访问完整界面。已启用的温度规则会随应用恢复运行；只运行安装器或 CLI 不会注册登录项。

**原生界面与监测。** 已包含全屏菜单栏弹窗修复。 原生 SwiftUI 主窗口与菜单栏弹窗保留紧凑布局。 紧凑双栏、搜索与测点详情、有限时调速弹窗、实际/目标转速和倒计时共用同一条后台数据流。关闭主窗口后在菜单栏继续运行；退出前如有本应用发起的调速，须确认恢复自动模式。 本机 M4 的 65 个测点可识别为 CPU、GPU、内存、SSD、无线网络与电源管理，其余测点保留为未知。命名依据与范围见 [传感器名称说明](docs/sensor-labels.md)。首次授权安装 Helper 后，Agent 无需 sudo 或重复弹窗即可控扇。 修复 SMC 异步读回导致的调速/恢复误报。已在 Mac16,10、macOS 27.0 上验证 1600 RPM 短时调速、到期恢复、主动恢复与 CLI 被终止后的恢复；其他机型、睡眠及 Ftst 路径仍未验收。详见 [硬件验收记录](docs/hardware-validation.md) 和 [温度有效性说明](docs/temperature-validation.md)。

## 给 Agent 一句话安装

> 帮我安装 https://github.com/timmyagentic/agentic-fan ，按照 INSTALL.md 安装 AgenticFan App、CLI 和 Codex skill，最后检查温度和风扇接口。

Agent 按 [INSTALL.md](INSTALL.md) 下载已构建的 DMG、校验安装包、安装 App、配置 `agenticfan` 命令和 skill，再运行只读诊断。也支持 Claude Code 或两者同时安装。安装器默认使用 `~/Applications`，不需要 Python 或本地编译。

```sh
bash scripts/install-macos.sh --agent codex
```

从 [最新正式 Release](https://github.com/timmyagentic/agentic-fan/releases/latest) 下载 `install-macos.sh`，阅读后执行 `bash install-macos.sh --agent codex`。默认安装匿名下载同一正式版的 DMG 与校验文件，不需要 GitHub 登录、Python 或本地编译；也支持 `--dmg PATH` 离线安装。

## 安装与运行

DMG 构建目标为 **Apple Silicon、macOS 15+**。取得构建产物后：

1. 打开 `AgenticFan-<version>-arm64.dmg`，把 `AgenticFan.app` 拖入 `Applications`。
2. 双击应用后，点击菜单栏 AgenticFan 图标查看概览；选择“打开主窗口”查看温度测点、风扇转速和模式。
3. 选择风扇、目标 RPM 与持续秒数，点击设置；首次控扇时由 macOS 请求管理员授权安装 Helper，后续无需重复授权。也可以点击“全部恢复自动”。

正式 Release 使用 Developer ID 签名、Apple 公证与装订票据，安装器会验证镜像、校验和、应用签名与 Gatekeeper。Actions 仍提供带 `-dev` 的开发包，不能代替正式 Release；构建方法见 [macOS 打包说明](docs/macos-packaging.md)。仓库中的 Python 包用于开发。

应用内置的 CLI 可直接供终端和 Agent 使用，默认输出 JSON，持续监控与控扇会话输出 NDJSON：

```sh
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" schema
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" capabilities
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" sensors
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" fans
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" watch --interval 1 --count 10
```

读取不要求管理员权限。首次启用控扇，以普通用户运行 `agenticfan helper install` 并完成一次系统授权；后续 Agent 直接调用 CLI，无需 `sudo`。升级 Helper 或卸载时可能再次需要授权。先读取 `fans` 返回的每个风扇的 `min_rpm`、`max_rpm` 和当前模式，再选择范围内的正整数 RPM；下面的 3000 只是示例值。

```sh
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" fans set --fan 0 --rpm 3000 --duration 30
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" fans auto --fan all
```

`fans set` 是**前台、有期限的会话**，不是永久设置。最多 3600 秒，必须明确指定 `--duration`。它依次输出目标已写入、实际 RPM 已达到容差、自动模式已恢复等事件。Ctrl-C 可以提前结束。另一终端运行 `fans auto` 也能结束相应控制。重复 `--fan` 可同时控制多个风扇，或使用 `--fan all`；所有选中风扇的范围会在第一次写入前检查。

## 无硬件演示

```sh
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" --gui --demo
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" --demo snapshot
```

在源码目录开发时，Python 3.10+ 可运行相同核心逻辑；Linux 也支持显式的 `--demo` 模拟模式：

```sh
python3 -m agenticfan --demo snapshot
python3 -m agenticfan --demo watch --interval 1 --count 3
python3 -m agenticfan --demo fans set --fan all --rpm 3000 --duration 3
python3 -m agenticfan --demo fans
```

全局选项 `--demo` 必须放在命令前。模拟输出总是含 `"simulated": true`，绝不因原生访问失败而自动回退。模拟状态保存在当前用户私有临时目录；测试可用 `AGENTICFAN_DEMO_DIR` 指定独立目录。这一变量不影响真实硬件的运行目录。

## Agent 接口

| 命令 | 用途 | 权限/输出 |
| --- | --- | --- |
| `schema` | 命令、参数、单位、事件语义与错误码 | 不需要硬件，单个 JSON |
| `doctor` | 安装与硬件只读诊断 | 检查报告中的可用状态，单个 JSON |
| `agent install --target codex/claude/both` | 配置当前用户的 CLI、PATH 和 skill | 从 App 内运行，用户配置写入，不改动风扇 |
| `capabilities` | 机型、架构、数据源、风扇及可用控制键 | 只读，单个 JSON |
| `sensors [--source all/smc/iohid]` | 温度读数、来源、时间戳和状态 | 只读，单个 JSON |
| `fans [list]` | 当前/目标/最低/最高 RPM、模式 | 只读，单个 JSON |
| `snapshot` | 一次采集温度与风扇 | 只读，单个 JSON |
| `watch --interval N [--count N]` | 连续快照 | 只读，NDJSON |
| `helper install/status/uninstall` | 安装、查看、卸载受限后台服务 | 安装/卸载需系统授权；状态只读 |
| `fans set --fan ID --rpm N --duration N` | 有时限的固定转速 | 启用 Helper 后普通用户调用，NDJSON |
| `fans auto --fan ID` | 恢复自动，`ID` 也可为 `all` | 启用 Helper 后普通用户调用，单个 JSON |

成功输出的公共字段为 `schema_version`、`ok`、`simulated`、`timestamp`、`data`；错误输出用 `error` 提供稳定的 `code`、可读 `message` 和 `details`。机器调用应同时检查 `ok` 和进程退出码。`--help` 与 `--version` 为常规文本输出。

读取的 `ok: true` 表示采集命令成功，不代表每个测点都有效。0.4.2 沿用 **JSON 协议 v2**：只有传感器 `status=ok` 时 `value` 才是可采用的数值，其余为 `null`；异常与零值保留在 `raw_value`。检查 `sensor_summary`、`partial`、每个传感器的 `reason` 和每个风扇的 `errors`。`doctor` 区分接口可读与数据可用，异常、待确认和不可用项有独立计数及警告。`control_available` 表示键、类型与范围具备调用条件，不等于通过真机验证。

Agent 建议流程与流式消费示例见 [docs/agent-interface.md](docs/agent-interface.md)。应用的“AI native”体现在可发现、可组合、带明确权限和生命周期的接口；这个 MVP 不包含聊天界面或自主温控模型。

可安装的 [agenticfan skill](agenticfan/agent_skill/SKILL.md) 将发现命令、诊断、监控、有限控扇和恢复自动模式连成 Agent 工作流程。它随 App 一起打包；安装器将它放入所选 Agent 的用户 skill 目录。

## 原生能力与边界

- 通过 IOKit AppleSMC 枚举 `T*` 温度类型键，并按每个键自身的 `sp78` 或 `flt ` 元数据解码；读取过程不改写任何键。
- 通过 IOHID 温度事件读取包括初代 M1 在内的可用测点。私有接口可能受 macOS 版本或访问策略影响；不可用会明确返回警告。
- 不复制商业应用的 XML 数据库。SMC 测点以原始键名标识，不声称已确认物理位置，也不保证所有 `T*` 键都是独立物理测点。IOHID ID 在本次系统启动内有效。
- 保留原始读数、来源与时间戳；有限非零值通过 −40～150°C 的宽范围检查才标为 `ok`，不是校准或安全温度保证。异常值为 `invalid`，零值为 `unverified`，读取失败为 `unavailable`；这些项的可用值均为 `null`。不补造休眠核心温度，不做未经定义的“核心平均温度”。
- 支持按键探测的 `F{i}Md` / `F{i}md` 模式、`flt ` / `fpe2` RPM，以及 Intel 的 `FS! ` 位图。Intel 路径也未经真机验收。
- 无风扇设备可以监控温度，不能设置风扇。暂不包含 SMART 磁盘、GPU 厂商私有接口、预设或温度曲线。

部分新机型可能需要全局 `Ftst` 路径。只有明确指定 `--experimental-ftst` 才会写入它；它要求所有风扇原先处于自动模式且 `Ftst` 为零。该路径未经真机验证，不能当作通用兼容承诺。恢复时仅在所有风扇都处于自动模式后清除它。若常规 `fans auto` 检测到它仍非零，会报告恢复未完成；显式恢复的形式为：

```sh
"/Applications/AgenticFan.app/Contents/MacOS/agenticfan" fans auto --fan all --experimental-ftst
```

## 控制生命周期

Helper 通过独立 guardian 进程执行整个写入与恢复过程。普通 CLI 连接本地 socket，Helper 维持 guardian 管道；CLI 断开或取消会关闭管道，触发恢复。独立进程使用单调时钟和墙上时钟检查时限；在管道 EOF（包括 CLI 被 SIGKILL）、正常取消、读写故障或采样间隔异常后尝试恢复自动，并读回模式验证。选中风扇已处于手动模式时拒绝接管。

SMC 模式和目标值可能异步生效；每步最多等待 3 秒读回，超时会恢复并返回具体观察值，准备时间计入会话时限。恢复自动也等待确认。目标写入与实际 RPM 到达是两个事件：必须在最多 15 秒或更短的会话时长内，观察到所有选中风扇达到 `max(100 RPM, 目标值的 8%)` 容差，否则恢复并报错。此检查不能证明整机散热效果。

锁只协调本工具，不协调其他风扇应用；验收与使用时应避免多个控制程序同时运行。杀死独立控制进程、内核调用永久阻塞、系统崩溃、固件行为或其他控制程序竞争，都可能妨碍恢复。Helper 由 launchd 管理；仍没有睡眠前通知处理；检测到恢复运行后的采样间隔异常会结束会话。不要把它当作硬件热保护机制。

真实写入的运行锁在 root 私有的 `/var/run/agenticfan`。Helper 文件为 root 所有，socket 仅对安装时授权的本机 UID 开放，并通过内核凭据复核调用者。接口仅包含状态、有限控扇和恢复，不接受命令、程序路径或任意 SMC 键；不配置免密 sudo，不访问网络。详情见 [Helper 与权限边界](docs/helper.md)。

关闭主窗口后，控扇会话继续在菜单栏运行。正常退出应用时，如有本应用发起且尚未确认恢复的控扇会话，会先要求恢复自动模式。GUI 首次安装 Helper 后启动普通权限 CLI；强制结束整个应用时，已有会话仍可能运行至指定时限。期限与恢复逻辑由独立控扇进程执行。

## 开发验证

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
python3 -m unittest discover -v
python3 packaging/macos/build_native.py
python3 -m agenticfan --gui --demo
```

窗口开发需要 macOS 15+ 和 Xcode Command Line Tools。先运行 `python3 packaging/macos/build_native.py`，再运行 `python3 -m agenticfan --gui`；`--demo` 显式使用模拟数据。SwiftUI/AppKit 界面位于 `native/`，通过原有 JSON CLI 监测和控制，不直接写 SMC。CLI 在不加载任何 UI 的情况下独立工作。

测试覆盖实际 CLI 子进程、读取协议、混合类型解码、部分写入失败、实际 RPM 未响应、并发控制、过期恢复以及父进程 SIGTERM/SIGKILL 后的恢复。DMG 流程另外检查独立运行时、窗口演示启动、应用签名与挂载后运行。硬件测试结果应单独记录；CI 的 macOS 虚拟机也不能代表真实 Mac SMC。

## 许可证

本项目源码采用 [MIT License](LICENSE)。

第三方代码保留原有版权与许可声明，详见 [第三方声明](agenticfan/THIRD_PARTY_NOTICES.md)。网站字体遵循各自的 [SIL Open Font License](website/public/brand/fonts/)。
