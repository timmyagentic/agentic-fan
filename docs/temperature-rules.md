# 温度规则与 Agent 配置

MacFan 0.5.0 使用一份用户本地 JSON，原生应用与 CLI 共用同一套校验和执行逻辑。默认路径为 `~/Library/Application Support/MacFan/rules.json`，`macfan rules show` 返回实际绝对路径、配置、修订号和运行状态。`--config PATH` 或环境变量 `MACFAN_RULES_PATH` 可指定另一份配置。不存在的文件显示以下默认值，默认关闭：

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

`fan` 是从零开始的实际风扇 ID。`target_rpm` 为 `"max"` 时，每次触发读取该风扇上报的最高转速，也可填范围内的整数 RPM。当前配置支持为一个风扇设置 1–16 条规则；多条触发时取较高目标。`sensor_group` 可选 CPU、GPU、内存、存储、无线、电源、电池、神经网络和系统对应的英文分组，完整枚举与 JSON Schema 在 `macfan schema` 的 `rules_configuration`。

温度达到 `trigger_celsius` 即触发；只有持续观测到温度 ≤`release_celsius` 满 `release_seconds` 才释放，期间反弹或观测间隔超过 3 秒会重计时。恢复温度必须低于触发温度。配置温度的 −40～150°C 范围只是软件合理性校验，不是硬件安全范围。CPU/GPU 使用已识别、有效测点的最高值，不是封装温度或平均温度。未知、零值、异常、无返回及被排除的校准参考读数不能触发规则。

Agent 可直接编辑上述文件；建议先读取修订号，在工作目录准备 JSON，再校验与原子应用：

```sh
macfan rules show
macfan rules validate --file ./rules.json
macfan rules apply --file ./rules.json --if-revision <刚读取的revision> --wait 8
macfan rules status
```

也支持 `rules apply --stdin`，传入 JSON；校验失败不覆盖已有配置。`--if-revision` 阻止覆盖另一 Agent 或界面刚保存的改动。重新读取、合并后再提交。启停已有配置可用 `macfan rules enable --wait 8` / `macfan rules disable --wait 8`。启用会授权正在运行的规则执行器按这些条件控扇；仅“保存”成功不代表执行器已运行或实际 RPM 已达到目标。

原生应用启动规则执行器；关闭主窗口后仍在菜单栏运行。应用不运行时，保存配置不会自动启动服务，Agent 也可运行前台命令 `macfan rules run`。`--parent-watch` 让 stdin 关闭时停止，适合进程拥有者管理。不要脱离输出启动无人观察的独立控扇命令。`rules status` 分别报告 `running`、`loaded_revision`、状态与 `control_confirmed`，超过 4 秒没有心跳标为 stale。`--wait` 的 `loaded` 只确认精确文件代次已被读取，不是 RPM 确认。

执行器热加载有效配置。非法文件或相关温度失效会释放本执行器持有的控制；修正后可重新读取。外部手动控制不会被接管；外部 `fans auto` 会暂停规则，显式重新应用或启用后才恢复。规则每次控扇仍通过既有 Helper/guardian 的有界会话；只有持续收到有效样本才发送心跳，独立 guardian 的 5 秒租约到期会尝试并读回自动模式。总会话仍有 3600 秒上限，到期后温度条件仍成立可开始下一次会话。退出、断开和故障时只恢复本执行器持有的控制，不修改其他控制器的手动状态。

新构建首次启用前可能需要通过系统管理员认证更新 Helper。应用退出会等待 `rules_finished`、`automatic_mode_verified: true` 和退出码 0；未确认时保留恢复提示。上述恢复是软件尽力保证，不能覆盖 guardian/kernel 故障或其他软件竞争，也不是硬件热保护。
