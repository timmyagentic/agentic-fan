# 温度规则与 Agent 配置

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

## Lark CLI 示例与试用参数

[固定通知程序](../examples/lark-temperature-hook.py)从stdin读事件，再以参数数组调用`lark-cli im +messages-send`，事件ID作为`--idempotency-key`。它拒绝发送模拟事件，默认`dry_run:true`，检查CLI退出状态及成功信封，不输出鉴权材料。它只通知，不改变风扇。程序需要可用的Python3；将shebang配置为本机绝对解释器路径，可避免菜单栏App的PATH差异。

把程序复制到固定本机路径，并将[配置样例](../examples/lark-temperature-hook.example.json)复制为程序旁同名`.json`。填写Lark CLI绝对路径、明确的`user`或`bot`身份、已确认的`chat_id`或`user_id`（二选一），测试请求后再显式关闭dry_run。账户认证由Lark CLI管理，App不内置飞书能力。使用真实接收人和身份前先确认授权；本仓库示例不包含收件人或凭据。

[高温规则样例](../examples/high-temperature-rules.json)为CPU≥100°C或GPU≥95°C持续10秒才触发最高转速；分别≤90°C/≤85°C持续20秒解除，所有活动规则解除后恢复系统自动。样例默认关闭、无Hook；这是可调整的试用策略，不是Apple温度上限或通用散热策略。
