# Agent 调用约定

先调用 `macfan schema` 发现命令。输出协议当前为 `schema_version: 1`。CLI 是本地工具，读写范围由命令限定，不含模型运行时或网络请求。

通过 DMG 安装后，可执行文件路径为 `/Applications/MacFan.app/Contents/MacOS/macfan`，无需 Python 环境。传入显式命令会进入 CLI；不带参数打开窗口。`--gui` 可以显式打开窗口。

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
    ["/Applications/MacFan.app/Contents/MacOS/macfan", "--demo", "fans", "set",
     "--fan", "0", "--rpm", "3000", "--duration", "3"],
    stdout=subprocess.PIPE, text=True,
)
for line in process.stdout:
    event = json.loads(line)
    print(event.get("event"), event["ok"], event.get("data", event.get("error")))
if process.wait() != 0:
    raise RuntimeError("Control session failed; inspect the structured error")
```

不要拼接 shell 字符串执行模型生成的参数，使用参数数组。真实写入要求 CLI 调用进程已取得 root 权限；CLI 不代为索取或保存管理员口令。桌面窗口通过 macOS 系统授权对话框启动特权 CLI，也不会接触或存储口令。

传感器输出示例：

```json
{
  "id": "smc:TCDX",
  "name": "TCDX",
  "source": "smc",
  "unit": "celsius",
  "value": 47.5,
  "status": "ok",
  "estimated": false,
  "sampled_at": "2026-01-01T00:00:00Z",
  "identity_scope": "machine",
  "label_verified": false
}
```

`ok` 状态仅表示有限数值读取成功，不证明校准、物理映射或散热安全。`unverified` 标注可能来自休眠测点的零读数；`unavailable` 的值为 `null`，并携带错误。IOHID 的 `identity_scope` 为 `boot`，跨重启应重新发现。

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
