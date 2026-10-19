# 温度有效性与 0.2.0 修复

此次修复依据用户的安装和只读排查反馈，不代表完整代码审计或任何机型已经通过控扇验收。用户观察到 IOHID `PMU tdev4`、`PMU tdev5` 返回约 −9200°C，系统事件描述也包含这些数值。现有证据支持“异常已存在于系统接口”，不能断言 AgenticFan 解码出错，也不能确定传感器未接入或驱动/固件故障。

0.1.0 仅检查有限数和零值，导致不合理数值进入 `ok` 与 `valid_sensor_count`；0.2.0 在 SMC 和 IOHID 共用的验证层修复这一问题，原有按键类型解码保留。

## 规则与边界

| 读数 | 状态 | 可用 `value` | 诊断证据 |
| --- | --- | --- | --- |
| 非零有限数，−40～150°C（含端点） | `ok` | 原始数值 | `raw_value`、来源、时间、未确认的名称 |
| 零值，包括负零 | `unverified` | `null` | `raw_value=0`、`reason=zero_reading` |
| 范围之外的有限数 | `invalid` | `null` | 原始值、`reason=outside_plausible_range` |
| NaN / 正负无穷 | `invalid` | `null` | `raw_value=null`、`raw_non_finite` 字符串 |
| 读取失败或无事件 | `unavailable` | `null` | 具体错误及 `read_failed` / `no_event` 原因 |

范围策略 ID 为 `mac-temperature-plausibility-v1`，随 `schema` 和样本统计公开。宽范围用于拦截明显不合理的内部温度，保留少量负数和偏高数值供调查；它是软件启发式，不是 Apple 硬件规格、过热阈值、校准保证或物理位置确认。处在此范围内的某些休眠、偏置或不支持的测点仍可能没有可信的物理意义。后续机型规则需要可复现的证据并另行版本化。

115.6°C 不因超过 100°C 被删除，`TCMz`、`Tp0c` 仍显示原始键名且 `label_verified=false`。本应用不根据外部逆向文档的“核心补偿”生成替代温度，不通过改端序来修复未定位的异常，也不隐去被拒绝的测点。读取失败后不复用旧值，恢复正常后按当前样本重新分类。

## 诊断与统计

`temperature_interface_available` 只要求至少一个接口返回数据；`monitoring_available` 要求至少一个 `ok` 读数。`sensor_summary` 在全部监控命令中保持一致，按状态和来源计数。`doctor.sensor_issues` 保留每个非 `ok` 项的原始证据，警告含稳定的代码、数量和测点 ID；数据异常与 I/O 错误分别可查。

以用户提供的数量构造回归样本：255 个测点中，若其余数值都通过规则，则为 **232 个 `ok`、2 个 `invalid`、20 个 `unverified`、1 个 `unavailable`**；254 个接口返回数据，整体 `status=limited`、`data_quality=partial`。232 仍仅表示通过软件检查，不是 232 个已校准的独立物理测点。该结果是测试复现，更新后的真机数量需重新采样确认。

## 升级后只读复查

按 [INSTALL.md](../INSTALL.md) 更新 App、CLI 和随包 skill。确认 `agenticfan --version` 为当前版本，`agenticfan schema` 的协议版本为 2，再运行：

```sh
agenticfan doctor
agenticfan sensors --source iohid
agenticfan watch --interval 1 --count 10
```

检查异常值是否出现在 `raw_value`、可用值是否为 `null`、告警是否列出对应 ID，以及统计和界面是否一致。机器状态可能变化，不要求每次都恰好 255 个测点或相同零值数量。以上操作不写入风扇。

真实转速响应、到期恢复及中断恢复仍需按 [硬件验收清单](hardware-validation.md) 单独验证。当前开发包仍未经过 Apple 公证；已有构建工具支持 Developer ID 签名和公证，但必须使用实际开发者身份与凭据完成，不通过关闭系统安全功能替代。
