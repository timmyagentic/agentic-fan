# 一次授权的本地 Helper

0.3.0 提供 `macfan helper install`、`helper status`、`helper uninstall`。安装和卸载通过 macOS 管理员授权；同版本且正在运行时重复安装不再弹框。后台服务由 launchd 管理。更新服务需要重新授权，已有手动会话必须先结束。普通 CLI 的设置与恢复调用不再使用 sudo；GUI 仅首次启用或需要升级 Helper 时启动安装流程。

## 信任边界

- 安装时把当前校验过签名的 App 复制到 `/Library/Application Support/MacFan/Helper.app`，包括独立运行时。复制后再校验签名；全部文件归 root 所有且不允许普通用户修改。拒绝指向包外的符号链接。后台进程和子进程不会从用户的 App、仓库、PYTHONPATH 或 PATH 加载代码。
- 服务使用 `/Library/LaunchDaemons/io.github.timmyagentic.macfan.helper.plist`。配置记录被授权的本机 UID；不会扩展到其他账户。安装失败会尝试恢复上一版本服务。
- `/var/run/macfan-helper` 是 root 所有的受保护目录。socket 仅授权 UID 可连接，服务另用内核 `getpeereid` 校验 UID；客户端也确认对端为 root。这是用户级授权，该用户的其他进程也能发起有限控扇请求，不声称按 App 签名隔离。
- 请求白名单仅有 `status`、`set`、`auto`。长度、字段、协议/版本、风扇ID、整数RPM和有限时长均校验；不接受 shell、可执行路径、原始 SMC 键、模拟模式或环境变量。实际 RPM 范围与当前模式由特权侧重新校验。
- 最多8个并发连接，首次读取有3秒超时；会话锁确保只有一个手动控扇会话。恢复命令可以在会话中执行。
- 不保存口令、不修改 sudoers、不降低 Gatekeeper 设置。服务使用固定命令和清理后的环境，不监听网络。

## 生命周期

`set` 启动独立 guardian，复用应用的硬件校验、锁、时限和恢复实现。客户端关闭连接、取消、Helper 退出会关闭 guardian 的输入管道，触发恢复；恢复完成后流式返回最终事件与退出码。launchd 配置保留脱离进程组的 guardian，以便服务退出时它仍有机会恢复。

模式切换、目标写入、自动恢复各自轮询读回（每步最多3秒），避免把异步写入误判成拒绝；不盲目重复写入。准备过程计入持续时间。实际转速仍需达到既定容差才发送 `control_confirmed`。

guardian 被强制杀死、内核调用阻塞、系统崩溃以及其他控扇程序竞争，仍可能妨碍恢复。没有睡眠前通知或自主热保护策略。自动恢复只能由实机模式读回证明；软件测试不能证明硬件兼容性。

## 卸载与更新

先结束手动会话，再运行 `macfan helper uninstall`，系统授权后移除服务、root运行时和socket。安装器保留上一份 root 运行时用于更新失败回退，卸载时一并移除；普通App/CLI/Skill独立保留。删掉用户App不会自动删掉系统Helper，完整卸载必须先执行此命令。

App升级后若Helper版本不匹配，控制请求明确拒绝并提示 `helper install`。读取能力继续可用。`helper status` 不弹授权框，报告服务版本、运行状态与兼容性。
