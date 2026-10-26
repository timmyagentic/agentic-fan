import Foundation
import CoreFoundation

struct Fan: Decodable, Identifiable {
    let id: Int
    let name: String
    let actualRpm: Double?
    let targetRpm: Double?
    let minRpm: Double?
    let maxRpm: Double?
    let mode: String
    let controlAvailable: Bool
    var title: String { name == "Fan 1" ? "排风扇" : name.replacingOccurrences(of: "Fan ", with: "风扇 ") }
    var canControl: Bool {
        controlAvailable && minRpm != nil && maxRpm != nil && minRpm! > 0 && maxRpm! > minRpm!
    }
}
struct Sensor: Decodable, Identifiable {
    let id: String
    let name: String
    let displayName: String?
    let group: String?
    let value: Double?
    let rawValue: Double?
    let status: String
    let source: String
    let labelNote: String?
    var title: String { displayName ?? name }
    var usableValue: Double? { status == "ok" && value?.isFinite == true ? value : nil }
    var statusText: String {
        ["ok": "可用", "invalid": "异常读数", "unverified": "待确认", "unavailable": "不可用"][status] ?? status
    }
}
struct Component: Decodable, Identifiable {
    let group: String
    let name: String
    let value: Double?
    let sensorCount: Int
    let validCount: Int
    var id: String { group }
    var title: String {
        ["cpu": "CPU 最高测点", "gpu": "GPU 最高测点", "memory": "内存附近", "storage": "SSD / 闪存",
         "wireless": "无线网络", "power": "电源管理"][group] ?? name
    }
    var symbol: String {
        ["cpu": "cpu", "gpu": "rectangle.3.group", "memory": "memorychip", "storage": "internaldrive",
         "wireless": "wifi", "power": "bolt"][group] ?? "thermometer.medium"
    }
}
struct Snapshot: Decodable {
    let sensors: [Sensor]
    let fans: [Fan]
    let temperatureOverview: [Component]
    let rules: RulesRuntime?
}
enum RuleTarget: Decodable, Equatable {
    case maximum
    case rpm(Int)
    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let name = try? value.decode(String.self), name == "max" { self = .maximum }
        else { self = .rpm(try value.decode(Int.self)) }
    }
    var title: String {
        switch self {
        case .maximum: return "最高转速"
        case .rpm(let value): return "\(value) RPM"
        }
    }
}
struct TemperatureRule: Decodable, Identifiable {
    let id: String
    let enabled: Bool
    let sensorGroup: String
    let triggerCelsius: Double
    let triggerSeconds: Double?
    let releaseCelsius: Double
    let releaseSeconds: Double
    let targetRpm: RuleTarget
    var summary: String {
        let name = ["cpu": "CPU", "gpu": "GPU", "memory": "内存附近", "storage": "SSD", "wireless": "无线网络", "power": "电源管理"][sensorGroup] ?? sensorGroup
        let hold = (triggerSeconds ?? 0) > 0 ? " 持续 \(ruleNumber(triggerSeconds!)) 秒" : ""
        return "\(name) ≥\(ruleNumber(triggerCelsius))°C\(hold) → \(targetRpm.title)；≤\(ruleNumber(releaseCelsius))°C 持续 \(ruleNumber(releaseSeconds)) 秒后解除"
    }
}
struct RulesConfiguration: Decodable {
    let version: Int
    let enabled: Bool
    let fan: Int
    let rules: [TemperatureRule]
    let hook: RuleHookConfiguration?
}
struct RuleHookConfiguration: Decodable {
    let program: String
    let timeoutSeconds: Double?
}
struct RulesRuntime: Decodable {
    let enabled: Bool
    let status: String
    let configPath: String
    let loadedRevision: String?
    let config: RulesConfiguration?
    let desiredRpm: Int?
    let activeRuleIds: [String]
    let error: String?
    let automaticModeVerified: Bool?
    let controlConfirmed: Bool
    let controlActive: Bool?
    let hook: RuleHookRuntime?
    var statusText: String {
        ["disabled": "规则已关闭", "watching": "规则监测中", "pending": "高温持续确认中", "active": "温度规则已触发", "cooling": "降温确认中", "restoring": "正在恢复系统自动", "paused": "规则已暂停", "error": "规则运行异常"][status] ?? status
    }
    var mayOwnControl: Bool { controlActive == true || desiredRpm != nil || ["active", "cooling", "restoring"].contains(status) }
    var hookSummary: String {
        guard let program = config?.hook?.program else { return "未启用 · 编辑阈值触发时执行的代码" }
        let name = URL(fileURLWithPath: program).lastPathComponent
        if !enabled { return "\(name) · 温度规则已关闭" }
        return "\(name) · \(hook?.summary ?? "等待规则触发")"
    }
    var hookNeedsAttention: Bool {
        config?.hook != nil && ((hook?.dropped ?? 0) > 0 || ["failed", "timed_out"].contains(hook?.lastResult?.status ?? ""))
    }
}
struct RuleHookRuntime: Decodable {
    struct Result: Decodable { let status: String }
    let running: Bool
    let queued: Int
    let dropped: Int
    let lastResult: Result?
    var summary: String? {
        if running { return "正在执行（等待 \(queued) 项）" }
        guard let result = lastResult else { return nil }
        let label = ["succeeded": "执行成功", "failed": "执行失败", "timed_out": "执行超时", "cancelled": "已取消"][result.status] ?? result.status
        return "上次\(label)" + (dropped > 0 ? "，已跳过 \(dropped) 个事件" : "")
    }
}
func ruleNumber(_ value: Double) -> String {
    String(format: value.rounded() == value ? "%.0f" : "%.1f", value)
}
func ruleDocument(_ object: Any) throws -> String {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
    return String(decoding: data, as: UTF8.self)
}
func ruleDocumentEnabled(_ document: String) -> Bool? {
    guard let data = document.data(using: .utf8),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let value = object["enabled"] as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else { return nil }
    return value.boolValue
}
func ruleDocument(_ document: String, enabled: Bool) throws -> String {
    let data = Data(document.utf8)
    guard var object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw NSError(domain: "AgenticFan", code: 2, userInfo: [NSLocalizedDescriptionKey: "规则必须是 JSON 对象。"])
    }
    object["enabled"] = enabled
    return try ruleDocument(object)
}
struct ControlReceipt {
    var target: Int?
    var deadline: Date?
    var started = false
    var confirmed = false
    var restored = false
    var recoveryRequired = false
    var error: String?

    mutating func receive(_ message: [String: Any]) {
        if let failure = message["error"] as? [String: Any] {
            error = failure["message"] as? String
            if (failure["code"] as? String ?? "").hasPrefix("RESTORE_") { recoveryRequired = true }
        }
        let data = message["data"] as? [String: Any] ?? [:]
        switch message["event"] as? String {
        case "control_started":
            started = true; recoveryRequired = true
            target = data["target_rpm"] as? Int
            if let time = data["expires_at"] as? String {
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                deadline = formatter.date(from: time)
            }
        case "control_confirmed": confirmed = true
        default: break
        }
        if data["automatic_mode_verified"] as? Bool == true { restored = true; recoveryRequired = false }
    }
    func completionError(exitCode: Int32) -> String? {
        if let error { return error }
        if exitCode != 0 { return "操作未完成（退出状态 \(exitCode)），请检查风扇状态。" }
        return restored ? nil : "尚未确认恢复自动模式，请点击恢复自动并检查风扇状态。"
    }
}
func temperature(_ value: Double?, precise: Bool = true) -> String {
    guard let value, value.isFinite else { return "—" }
    return String(format: precise ? "%.1f °C" : "%.0f°C", value)
}
func rpmText(_ value: Double?) -> String {
    guard let value, value.isFinite else { return "—" }
    return String(format: "%.0f", value)
}
func controlArguments(fan: Fan, rpm: Int, duration: Int) throws -> [String] {
    guard fan.canControl, (Int(fan.minRpm!)...Int(fan.maxRpm!)).contains(rpm), (1...3600).contains(duration) else {
        throw NSError(domain: "AgenticFan", code: 2, userInfo: [NSLocalizedDescriptionKey: "请选择范围内的转速与 1–3600 秒的持续时间。"])
    }
    return ["fans", "set", "--fan", String(fan.id), "--rpm", String(rpm), "--duration", String(duration)]
}
