# Agent 调用约定

先调用 `agenticfan schema` 发现命令。下一版 AgenticFan 0.4.2 的输出协议为 `schema_version: 2`；升级后应重新读取约定。CLI 是本地工具，读写范围由命令限定，不含模型运行时或网络请求。

通过 DMG 安装后，可执行文件路径为 `/Applications/AgenticFan.app/Contents/MacOS/agenticfan`，无需 Python 环境。传入显式命令会进入 CLI；不带参数打开窗口。`--gui` 可以显式打开窗口。

通过 [Agent 安装入口](../INSTALL.md) 安装时，App 默认位于 `~/Applications`，CLI 位于 `~/.local/bin/agenticfan`。Codex 的 `~/.agents/skills/agenticfan` 或 Claude Code 的 `~/.claude/skills/agenticfan` 中会安装 skill 及记录绝对可执行文件路径的 `installation.json`。当前进程可直接使用绝对路径，无需等待 PATH 更新。

`doctor` 总是尽可能返回结构化诊断。`ok: true` 表示诊断完成。`temperature_interface_available` 表示至少一个接口返回读数，`monitoring_available` 表示至少一个测点通过合理性检查；两者可以不同。`data_quality` 描述当前温度样本质量；整体 `status` 在仍有可用读数但存在警告、错误或被排除读数时为 `limited`，全部无可用温度时为 `unavailable`。检查 `warnings`、`errors` 和带原始证据的 `sensor_issues`，不要只判断进程退出码。诊断不会触发管理员授权或风扇写入；`hardware_write_tested` 为 `false`。`control_interface_available` 仅是接口发现结果。

建议顺序：

1. 调用 `capabilities`，确认平台、风扇数量、读数来源和错误。
2. 调用 `snapshot` 或短期 `watch`，只把有效、当前的测点当作观测；SMC 键与物理位置的映射尚未验证。
3. 有用户授权的控扇任务才调用 `fans set`。使用返回的范围选择 RPM，明确给出持续时间。
4. 持续读取 NDJSON 到进程结束。`control_started` 只表示写入的模式和目标通过了读回；`control_confirmed` 才表示实际 RPM 曾达到容差。
5. 确认最后的 `control_finished` 与退出码 0。若退出码 7，自动模式恢复未能验证，应报告给用户，并在权限与硬件可用时显式恢复和复查。

示例流式调用（这里使用模拟器，无 root 权限）：

```python
import json
import subprocess

process = subprocess.Popen(
    ["/Applications/AgenticFan.app/Contents/MacOS/agenticfan", "--demo", "fans", "set",
     "--fan", "0", "--rpm", "3000", "--duration", "3"],
    stdout=subprocess.PIPE, text=True,
)
for line in process.stdout:
    event = json.loads(line)
    print(event.get("event"), event["ok"], event.get("data", event.get("error")))
if process.wait() != 0:
    raise RuntimeError("Control session failed; inspect the structured error")
```

不要拼接 shell 字符串执行模型生成的参数，使用参数数组。先用 `helper status` 确认后台服务；若尚未安装且用户已授权启用控扇，运行 `helper install` 完成一次系统授权。后续 `fans set` / `fans auto` 以普通用户调用。CLI 不索取或保存口令，不回退到 sudo；缺失或不兼容的 Helper 返回明确错误。窗口共用该 CLI 和 Helper。

传感器输出示例：

```json
{
  "id": "smc:TCDX",
  "name": "TCDX",
  "source": "smc",
  "unit": "celsius",
  "value": 47.5,
  "raw_value": 47.5,
  "status": "ok",
  "reason": null,
  "estimated": false,
  "sampled_at": "2026-01-01T00:00:00Z",
  "identity_scope": "machine",
  "label_verified": false
}
```

`ok` 表示非零有限数值通过公开的合理性范围检查，不证明校准、物理映射或散热安全。当前宽范围为 −40～150°C（含端点），不是 Apple 规格或安全温度范围。115.6°C 会保留，但不能仅凭键名解释为 CPU 温度。

v2 的迁移变化：`value` 只在 `status=ok` 时为数字；`invalid`、`unverified`、`unavailable` 均为 `null`。所有有限的接口原始值保留在 `raw_value`；非有限数值通过 `raw_non_finite` 字符串记录，避免生成非法 JSON。`reason` 区分 `outside_plausible_range`、`non_finite_reading`、`zero_reading`、`read_failed` 和 `no_event`。零值不能排除测点休眠或未激活的可能，不能补成别的温度。IOHID 的 `identity_scope` 为 `boot`，跨重启应重新发现；所有原始标签均未确认物理位置。

例如 `PMU tdev4` 返回 −9200°C 时，输出 `status=invalid`、`value=null`、`raw_value=-9200`、`reason=outside_plausible_range`。原始值只用于排查，不能用作平均值、最高温或控制依据；该分类本身不说明是驱动、固件、未接入测点还是解码问题。

`sensors`、`snapshot`、`watch`、`capabilities`、`doctor` 共用 `sensor_summary`：`total` 是发现数，`readable` 包含异常和待确认值，`ok` 才是通过检查数；另列 `invalid`、`unverified`、`unavailable` 及 `by_source` 分项。`data_quality` 为 `complete`（全部通过）、`partial`（部分通过）、`unusable`（接口返回了数据但无一通过）或 `unavailable`（无读数）。`valid_sensor_count` 保留为 `ok` 数量，不能理解为已验证的独立物理传感器数量。`--source` 的统计仅涵盖选中的来源。详见 [温度有效性规则](temperature-validation.md)。

`watch` 的时间戳可判断新鲜度，但快照按顺序读取，并非硬件原子快照。`fans set` 不根据温度做自主决策，不会续期，也不会在退出后长期保留人工设置。请勿并行运行其他风扇控制程序。

| 退出码 | 含义 |
| --- | --- |
| 0 | 命令成功；流式任务仍应读取最终事件 |
| 2 | 参数或范围不合法 |
| 3 | 平台、测点或控制接口不可用 |
| 4 | 缺少硬件写入权限 |
| 5 | I/O、目标未达到、控制丢失或内部错误 |
| 6 | 会话冲突或风扇已经处于手动模式 |
| 7 | 自动恢复未能确认 |
| 130 | 调用被中断；仍需检查恢复事件 |

`doctor` 新增 `helper` 与 `control_ready`。`write_requires_root` 为 false，`hardware_write_requires_privilege` 为 true：权限在服务端，Agent 本身不提权。Helper 授权边界是本机用户 UID；该用户的进程均可发起限定请求。

温度行增加 `display_name`、`group`、`group_name`、`label_status`、`label_source`、`label_note`。`temperature_overview` 提供按部件分组的有效已识别测点最高值与来源ID。可用 `sensors --group cpu` 等过滤；原 `id`、`name` 和原始读数保持不变。详情见 [命名说明](sensor-labels.md)。

## 可编辑温度规则

`rules show/validate/apply/enable/disable/status/run` 与原生界面共享用户本地 JSON。配置 Schema 在 `schema.rules_configuration`；字段、原子更新、修订冲突、状态回执与控制生命周期见[温度规则](temperature-rules.md)。
