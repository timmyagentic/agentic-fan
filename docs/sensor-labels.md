# 温度测点名称

0.3.1 默认按 CPU、GPU、内存附近、SSD/闪存、无线网络、电源管理分组；主页面显示 CPU/GPU/SSD 的已识别有效测点最高值。点击部件展开具体测点，选中一行查看原始名称、接口来源、原始读数和命名说明。高级开关显示原始标识、命名依据与所有未识别测点。

命名来源：

- M4 / M4 Pro / M4 Max 按芯片型号精确选择公开映射，不按 `T*` 前缀猜测位置。少量键名映射参考 MIT 开源的 [Stats](https://github.com/exelban/stats/blob/4ecf4f736056eb488c75aa2fa62e749abd87c4a4/Modules/Sensors/values.swift)，许可证随 App 打包在 agenticfan/THIRD_PARTY_NOTICES.md。
- IOHID 的 NAND、PMU、CPU/GPU 等 Product 名称翻译成中文，保留系统编号。PMU tcal 参考读数不纳入温度概览。
- 没有适用映射的键继续显示为未识别，例如本机的 TCMb、TCMz、TPD0；没有把它们猜成 CPU 总温度或某个接口。
- 测点编号只区分读数，不代表物理 CPU 核心编号或数量。`label_verified` 仍为 false；命名不等于物理安装位置/校准验证。

JSON v2 保留原来的 id/name/value/raw_value/status，只增加 display_name、group、group_name、label_status、label_source、label_note。`label_status` 为 community_mapping、system_name、unknown 或 simulated。`temperature_overview` 的 value 是本组当前有效且已识别测点的最大值，附带 sensor_ids 和 value_sensor_id；未知值、无效值、零值及 tcal 参考行不参与，缺数据时为 null。不是独立封装温度或平均温度。

本机 Mac16,10 / Apple M4 / macOS27.0 的214个读数中，65个具有命名依据：CPU12、GPU8、内存3、存储2、无线1、电源管理39。其余149个放入高级视图。数量不等于独立物理传感器或核心数量，其他型号的映射覆盖率可能不同。
