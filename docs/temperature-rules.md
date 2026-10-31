# 温度规则与 Agent 配置

## 自定义温度—转速曲线

原生应用的“编辑…”入口提供系统自动、自定义曲线和已有温度规则三种方式。曲线支持添加、删除、拖动节点，也可直接编辑温度与 RPM；保存并应用后才生效。切回系统自动会停止本应用持有的控制，保留已保存的曲线、阈值规则和 Hook。

[曲线配置示例](../examples/temperature-curve.json)默认关闭，数值仅用于说明，须按 `agenticfan fans` 返回的实际 RPM 范围和用户批准的参数调整。顶层 `mode: "curve"` 选择曲线，`enabled: false` 选择系统自动；缺少 `mode` 时保持旧版阈值行为。`curve.sensor_group` 选择一组已识别有效测点，`nodes` 需要 2–16 个节点，每个节点为 `temperature_celsius` 与整数 `target_rpm`。温度严格递增，转速可持平或增加；所有节点都须在选中风扇的硬件范围内，不能只验证当前插值结果。

节点之间线性插值，例如 80°C/2200 RPM 与 90°C/3200 RPM 之间，85°C 对应 2700 RPM；低于首节点或高于末节点时保持端点转速，不会在端点反复切换系统自动。温度来源仍是分组内有效已识别测点的最高值，不是封装温度或平均值。

`ramp_rpm_per_second` 限制升降转速速度，范围 1–1000，默认每秒 200 RPM。执行器从观测到的当前转速开始逐步接近插值目标；迟到采样不会补成大幅跳变。运行态的 `curve_target_rpm` 为曲线目标，`desired_rpm` 为当前平滑请求。程序限制的是下发目标，物理风扇响应与噪音需要单独实机验证。

0.4.5 新增 `control_feedback`，用于持续跟随状态。Helper 每秒先读取当前已生效目标的实际转速，再下发下一目标，避免将旧测量归到新请求。反馈包含该次 `sequence`、`target_rpm`、`actual_rpm`、`sampled_at`、`sampled_monotonic`、`tolerance_rpm`、`within_tolerance`，运行态另附 `status`、`age_seconds` 和 `pending_update`。Helper 事件中的 `actual_rpm` 按风扇 ID 映射，规则运行态则是所选风扇的单个数值。

- `tracking`：最近有效采样在 `max(100 RPM, 目标的 8%)` 容差内；即使 `pending_update:true`，仍可表示正在正常跟随已下发的目标。
- `adjusting`：最近采样未达到该目标容差。持续偏离达到 15 秒会报错并恢复自动模式，曾经确认成功或不断更新目标均不能清除连续失配计时。
- `waiting` / `stale` / `stopped`：分别是尚无反馈、反馈超过三秒（或时间无效）、当前控制会话的跟随监测已结束，均不能表示正常跟随。`stopped` 不代替自动恢复回执。

`control_confirmed` 仍只确认**最新请求序号**：只有新鲜反馈对应当前目标才为 true；持续调速时它可能为 false，而 `control_feedback.status` 为 `tracking`。原生界面用持续反馈显示同一次采样的已下发／实际 RPM。`rules status` 会重新检查反馈年龄，其他消费者也须让旧快照过期；`sampled_monotonic` 只在本机本次启动内可比较。新会话不继承旧会话的反馈，停止后等待 `rules_finished.automatic_mode_verified` 确认恢复。

曲线复用 `rules show/validate/apply/status/run` 和同一修订保护机制，CLI 无需另一份配置。曲线与阈值规则互斥执行，曲线模式保留但不执行阈值 Hook。切档在同一 Helper/guardian 会话内只更新选中风扇的目标，不反复恢复自动；更新不延长 3600 秒硬期限和 5 秒心跳租约。失效/过期数据、非法配置、停止或期限到达仍释放本执行器持有的控制。外部自动请求仍暂停运行，需明确重新应用才恢复。

## 阈值规则

AgenticFan 使用一份用户本地 JSON，原生应用与 CLI 共用同一套校验和执行逻辑。默认路径为 `~/Library/Application Support/AgenticFan/rules.json`，`agenticfan rules show` 返回实际绝对路径、配置、修订号和运行状态。`--config PATH` 或环境变量 `AGENTICFAN_RULES_PATH` 可指定另一份配置。不存在的文件显示以下默认值，默认关闭：

```json
{
  "version": 1,
  "enabled": false,
  "fan": 0,
  "rules": [{
    "id": "cpu-hot",
    "enabled": true,
    "sensor_group": "cpu",
    "trigger_celsius": 99,
    "release_celsius": 95,
    "release_seconds": 5,
    "target_rpm": "max"
  }]
}
```

`fan` 是从零开始的实际风扇 ID。`target_rpm` 为 `"max"` 时，每次触发读取该风扇上报的最高转速，也可填范围内的整数 RPM。当前配置支持为一个风扇设置 1–16 条规则；多条触发时取较高目标。`sensor_group` 可选 CPU、GPU、内存、存储、无线、电源、电池、神经网络和系统对应的英文分组，完整枚举与 JSON Schema 在 `agenticfan schema` 的 `rules_configuration`。

温度达到 `trigger_celsius` 并持续 `trigger_seconds` 秒才触发（可选，0–300 秒，省略时保持旧版即时触发）；触发前跌回阈值以下、失效读数或采样间隔超过 3 秒都会重新计时。只有持续观测到温度 ≤`release_celsius` 满 `release_seconds` 才释放，期间反弹或观测间隔超过 3 秒会重计时。恢复温度必须低于触发温度。配置温度的 −40～150°C 范围只是软件合理性校验，不是硬件安全范围。CPU/GPU 使用已识别、有效测点的最高值，不是封装温度或平均温度。未知、零值、异常、无返回及被排除的校准参考读数不能触发规则。

Agent 可直接编辑上述文件；建议先读取修订号，在工作目录准备 JSON，再校验与原子应用：

```sh
agenticfan rules show
agenticfan rules validate --file ./rules.json
agenticfan rules apply --file ./rules.json --if-revision <刚读取的revision> --wait 8
agenticfan rules status
```

也支持 `rules apply --stdin`，传入 JSON；校验失败不覆盖已有配置。`--if-revision` 阻止覆盖另一 Agent 或界面刚保存的改动。重新读取、合并后再提交。启停已有配置可用 `agenticfan rules enable --wait 8` / `agenticfan rules disable --wait 8`。启用会授权正在运行的规则执行器按这些条件控扇；仅“保存”成功不代表执行器已运行或实际 RPM 已达到目标。

原生应用启动规则执行器；关闭主窗口后仍在菜单栏运行。应用不运行时，保存配置不会自动启动服务，Agent 也可运行前台命令 `agenticfan rules run`。`--parent-watch` 让 stdin 关闭时停止，适合进程拥有者管理。不要脱离输出启动无人观察的独立控扇命令。`rules status` 分别报告 `running`、`loaded_revision`、状态与 `control_confirmed`，超过 4 秒没有心跳标为 stale。`--wait` 的 `loaded` 只确认精确文件代次已被读取，不是 RPM 确认。

执行器热加载有效配置。非法文件或相关温度失效会释放本执行器持有的控制；修正后可重新读取。外部手动控制不会被接管；外部 `fans auto` 会暂停规则，显式重新应用或启用后才恢复。规则每次控扇仍通过既有 Helper/guardian 的有界会话；只有持续收到有效样本才发送心跳，独立 guardian 的 5 秒租约到期会尝试并读回自动模式。总会话仍有 3600 秒上限，到期后温度条件仍成立可开始下一次会话。退出、断开和故障时只恢复本执行器持有的控制，不修改其他控制器的手动状态。

新构建首次启用前可能需要通过系统管理员认证更新 Helper。应用退出会等待 `rules_finished`、`automatic_mode_verified: true` 和退出码 0；未确认时保留恢复提示。上述恢复是软件尽力保证，不能覆盖 guardian/kernel 故障或其他软件竞争，也不是硬件热保护。


## 固定程序 Hook

Hook 是一个通用的阈值事件执行入口。温度规则达到持续触发条件后，运行预先配置的本机代码；代码可以记录数据、调用 API、启动其他工具或完成使用者需要的动作。发送飞书消息只是其中一个脚本示例，App不限定Hook的业务用途。

在原生主窗口左栏底部或菜单栏弹窗中点击 **阈值 Hook**，直接编辑脚本内容，再点击 **保存脚本与设置**（⌘S）。新配置使用规则文件旁的 `hooks/threshold-hook.sh`；默认只有解释器声明和注释，不执行任何动作。打开编辑器不会写入文件，首次保存时才创建；应用自动设置执行权限，之后始终载入已保存的内容。可以点击 **打开所在位置** 在外部编辑这个文件。

```sh
#!/bin/sh
# 在这里编写温度规则触发后要执行的代码。
# 事件 JSON 通过标准输入传入；默认不执行任何动作。
```

没有解释器声明的空白内容或普通命令会自动加上 `#!/bin/sh`。也可自行使用 Python、JavaScript 等解释器的 shebang，建议指定本机可用的绝对解释器路径。需要事件时，在 Shell 中用 `event=$(cat)` 读取标准输入，或在 Python 中用 `json.load(sys.stdin)`；真实外部动作前可检查事件的 `simulated` 标记。代码执行的业务动作不限于通知。

已有 Hook 指向 UTF-8 文本脚本时，直接编辑原文件，保留原目录及相邻依赖。二进制程序、符号链接或大于 256 KB 的文件不会被编辑器改写；可明确点击 **使用默认脚本**，保存设置后才切换，原文件仍保留。高级用户仍可通过规则 JSON 配置其他可执行程序。

编辑器检查脚本是否被外部修改，并原子保存完整文件；发生冲突时要求重新读取，避免旧草稿覆盖新代码。规则设置仍使用修订检查，保留其他温度规则及其启用状态。脚本和设置分别报告保存结果：脚本保存后，下次事件会读取最新代码；若设置应用失败，界面明确提示，避免把两个独立文件的写入误认为同一事务。**完成**不保存草稿，默认脚本保持关闭。

入口显示程序名、等待触发或上次执行结果；执行失败和事件丢弃会提示检查。程序退出成功只表示退出状态为0，业务动作是否完成由代码自己确认。界面不会为了测试而直接运行任意程序或降低温度阈值，详细日志由Hook代码写入自己的文件。

App默认在首次正常启动时开启登录自启。注册失败会在设置显示错误并在后续启动重试；主动关闭的选择会保留。登录后，App从同一持久配置文件恢复已开启的温度规则和Hook；需保证Helper兼容、程序及其解释器/CLI仍可用，代码所依赖的外部服务仍有权限。真正的重启登录验收与模拟或App重启检查分开记录。

在配置顶层增加 `"hook": {"program": "/absolute/path/to/executable", "timeout_seconds": 10}`；省略或设为 `null` 时关闭。路径必须为受信任本机可执行文件的绝对路径；不接受shell命令串，不插值参数，不使用root Helper运行。程序以当前用户执行，工作目录为程序父目录，继承用户环境但清除冻结Python运行时变量。程序可使用自身已有的Lark CLI登录态，不在规则文件中保存凭据。

每条规则由未触发变为激活时，stdin收到一行JSON，例如：

```json
{"version":1,"event":"rule_triggered","event_id":"4a34bc92-10ac-431b-8369-a9f0660af3fd","timestamp":1792911600,"simulated":false,"rule":{"id":"cpu-hot","enabled":true,"sensor_group":"cpu","trigger_celsius":100,"trigger_seconds":10,"release_celsius":90,"release_seconds":20,"target_rpm":"max"},"temperature_celsius":101.2,"fan":0,"target_rpm":4900,"control_status":"active","control_confirmed":false}
```

事件表达温度条件成立，不代表已达到目标RPM；外部控制占用时也可通知，此时`control_status`为`paused`。每次激活只执行一次，相同内容重写配置不重复通知。真实修改该规则后重新计时，其他未变规则保留状态。重启运行器或采样失效后重新满足完整条件会产生新事件；本期不承诺跨重启exactly-once投递。

独立worker串行运行，最多排队16项。超时范围0.1–60秒，默认10秒；退出非零、文件缺失、超时和队列满仅记录Hook状态，不暂停采样和控扇。满队列丢弃新事件，不无限堆积，不自动重试；`hook.last_result`、`queued`、`running`、`dropped`可用于诊断。输出丢弃，避免无限日志及泄露凭据；外部程序需要详细日志时自行管理。禁用规则、更换/移除Hook会取消等待和执行中的任务；退出时先恢复本运行器持有的控扇，再结束Hook及其同进程组子进程。此机制不是任意程序的沙箱；不支持自行脱离进程组长期驻留的Hook。

## Agent 与界面共用的 Hook 接口

0.4.4 的原生编辑器通过同一 CLI 读取、保存、检查草稿及刷新状态；文件路径、模板、权限和冲突规则均在共享核心。`agenticfan schema` 的 `hook_files` 提供字段、默认模板、错误码与语义，随安装包分发的 Skill 给出完整流程。

| 命令 | 行为 |
| --- | --- |
| `hook show` | 返回默认/当前文件位置、内容、是否存在/可编辑/可执行、脚本修订与规则修订、启停设置和执行记录；不创建文件 |
| `hook apply` | 校验并原子保存代码及执行权限，按显式选项更新 Hook 设置；默认保留原启停状态，不修改温度规则总开关 |
| `hook validate` | 检查已保存文件，或用 `--stdin`/`--file` 检查草稿；不运行用户脚本 |
| `hook status` | 返回文件元数据、校验结果、运行器状态和执行版本记录，不包含代码，也不创建文件 |

四个命令都支持 `--config /absolute/rules.json`。当没有配置 Hook 时，默认文件为规则文件旁的 `hooks/threshold-hook.sh`。已有 Hook 默认选中其原文件；`--default` 显式选择默认文件，读取或仅保存它不会自动替换已配置的程序。

`hook apply` 和 `hook validate` 的 `--stdin`/`--file` 接收 **UTF-8 脚本原文**，不是 JSON。先读到修订，再校验和应用：

```sh
agenticfan hook show
agenticfan hook validate --file /absolute/draft.sh
agenticfan hook apply --file /absolute/draft.sh \
  --if-revision <script.revision> \
  --if-rules-revision <settings.rules_revision>
agenticfan hook validate
agenticfan hook status
```

脚本修订是文件字节的 SHA-256，新文件使用字面量 `missing`；规则修订独立计算。内容写入和启用都要求这两个修订，防止旧内容覆盖其他编辑器或把另一份脚本误设为当前 Hook。显式启用时追加 `--enable --timeout-seconds 15 --wait 8`（按实际需求设置超时）；关闭使用 `hook apply --disable --if-rules-revision <revision>`，保留脚本文件和其他规则。`HOOK_CONFLICT`、`CONFIG_CONFLICT` 或 `HOOK_BUSY` 要求重新读取/合并/重试。

`hook validate` 无输入时还检查文件存在、可执行权限和解释器可用性；有输入时检查草稿，并报告是否添加了默认 shebang。固定系统 sh/bash 使用不执行模式检查语法；Python使用当前CLI的AST解析器，其他语言明确返回 `syntax.status: unsupported`，不会调用任意解释器做检查。错误返回退出码2和`error.details.validation`；查看`issues`及`warnings`，不能把未支持的检查当成通过。shebang使用LF换行；`env`解释器依赖当前进程PATH，多参数需用`env -S`。检查不证明另一进程环境、外部依赖或业务动作成功。

应用回执分别报告`script_saved`/`script_saved_revision`、`settings_saved`、`rules_revision`/`rules_generation`与`rules_loaded`。脚本和规则是两个文件，`HOOK_APPLY_PARTIAL`会保留已成功写入的事实并要求回读，不伪装成全部回滚。只保存代码不重写规则JSON；文件更新后，运行器处理下一个事件时读取最新代码。加载规则设置不等于执行了脚本，CLI不会为验证而降低温度阈值或直接试跑。

执行回执包含`program`、`script_revision`、`script_revision_after`、起止时间、耗时、退出状态及`script_changed_during_execution`。监测进程停止后仍保留最后回执，退出时会更新被取消的执行结果。`hook status`将这些观测与当前文件对比，`last_observed_revision_matches_current`没有可比对证据时为null。前后观测不是不可变执行快照，外部编辑器仍可能竞争；版本一致也不表示业务动作成功，需同时查看`last_result.status`及程序自己的结果。无法追踪的超大/特殊可执行文件可缺少版本观测。

`rules validate`仍负责规则JSON结构，脚本可执行性与语法由`hook validate`负责。合成事件的`simulated:true`只是输入标记，任意用户代码仍可执行真实动作；只有在相应动作已获授权时才安排实际试跑。

## Lark CLI 示例与试用参数

[固定通知程序](../examples/lark-temperature-hook.py)从stdin读事件，再以参数数组调用`lark-cli im +messages-send`，事件ID作为`--idempotency-key`。它拒绝发送模拟事件，默认`dry_run:true`，检查CLI退出状态及成功信封，不输出鉴权材料。它只通知，不改变风扇。程序需要可用的Python3；将shebang配置为本机绝对解释器路径，可避免菜单栏App的PATH差异。

把程序复制到固定本机路径，并将[配置样例](../examples/lark-temperature-hook.example.json)复制为程序旁同名`.json`。填写Lark CLI绝对路径、明确的`user`或`bot`身份、已确认的`chat_id`或`user_id`（二选一），测试请求后再显式关闭dry_run。账户认证由Lark CLI管理，App不内置飞书能力。使用真实接收人和身份前先确认授权；本仓库示例不包含收件人或凭据。

[高温规则样例](../examples/high-temperature-rules.json)为CPU≥100°C或GPU≥95°C持续10秒才触发最高转速；分别≤90°C/≤85°C持续20秒解除，所有活动规则解除后恢复系统自动。样例默认关闭、无Hook；这是可调整的试用策略，不是Apple温度上限或通用散热策略。
