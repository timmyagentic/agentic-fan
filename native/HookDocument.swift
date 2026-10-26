import Foundation

struct HookIssue: Decodable {
    let code: String
    let message: String
}

struct HookValidation: Decodable {
    struct Syntax: Decodable { let status: String; let checker: String? }
    let valid: Bool
    let scope: String
    let syntax: Syntax
    let issues: [HookIssue]
    let warnings: [HookIssue]
    var summary: String {
        if !valid { return "检查未通过：" + issues.map(\.message).joined(separator: "；") }
        if syntax.status == "unsupported" { return "文件条件通过；此解释器的语法尚未检查。" }
        return "检查通过，未执行脚本。" + (warnings.isEmpty ? "" : " 部分环境或语法条件尚未确认。")
    }
}

struct HookDocument: Decodable {
    struct Script: Decodable {
        let path: String
        let defaultPath: String
        let exists: Bool
        let editable: Bool
        let executable: Bool
        let revision: String?
        let content: String?
        let issue: HookIssue?
    }
    struct Settings: Decodable {
        let enabled: Bool
        let timeoutSeconds: Double
        let rulesEnabled: Bool
        let rulesRevision: String
        let selectedProgramIsConfigured: Bool
    }
    struct Execution: Decodable {
        struct Result: Decodable { let status: String; let scriptRevision: String?; let exitCode: Int? }
        let runnerRunning: Bool
        let running: Bool
        let queued: Int
        let dropped: Int
        let lastResult: Result?
        let lastObservedRevisionMatchesCurrent: Bool?
    }
    let script: Script
    let settings: Settings
    let execution: Execution
    let validation: HookValidation?

    func applyOptions(enabled: Bool, timeout: Double, useDefault: Bool, includeContent: Bool = true) throws -> [String] {
        var args = ["--if-rules-revision", settings.rulesRevision, enabled ? "--enable" : "--disable",
                    "--timeout-seconds", String(timeout), "--wait", "8"]
        if useDefault { args.append("--default") }
        if (script.editable && includeContent) || enabled {
            guard let revision = script.revision else { throw NSError(domain: "AgenticFan", code: 3,
                userInfo: [NSLocalizedDescriptionKey: "后台没有返回脚本修订号，请重新读取。"]) }
            args += ["--if-revision", revision]
            if script.editable && includeContent { args.append("--stdin") }
        }
        return args
    }
    var executionSummary: String {
        if !settings.rulesEnabled { return "温度规则已关闭" }
        if !settings.selectedProgramIsConfigured { return "Hook 未启用" }
        if !execution.runnerRunning { return "监测进程未运行" }
        if execution.running { return "正在执行（等待 \(execution.queued) 项）" }
        guard let last = execution.lastResult else { return "等待规则触发" }
        let status = ["succeeded": "执行成功", "failed": "执行失败", "timed_out": "执行超时", "cancelled": "已取消"][last.status] ?? last.status
        return "上次\(status)" + (execution.dropped > 0 ? "，已跳过 \(execution.dropped) 个事件" : "")
    }
    var revisionSummary: String {
        guard let matches = execution.lastObservedRevisionMatchesCurrent else { return "尚无可比对的脚本执行记录。" }
        return matches ? "上次执行前后读取的文件版本与当前一致。" : "文件版本已有变化，等待新的执行回执。"
    }
}
