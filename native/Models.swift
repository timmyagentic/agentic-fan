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
    let mode: String?
    let curve: TemperatureCurve?
}
struct CurveNode: Decodable, Identifiable, Equatable {
    var id = UUID()
    var temperatureCelsius: Double
    var targetRpm: Int
    enum CodingKeys: String, CodingKey { case temperatureCelsius, targetRpm }
    init(_ temperature: Double, _ rpm: Int) { temperatureCelsius = temperature; targetRpm = rpm }
}
struct TemperatureCurve: Decodable {
    let sensorGroup: String
    let nodes: [CurveNode]
    let rampRpmPerSecond: Double?
    var summary: String { "\(sensorGroup.uppercased()) · \(nodes.count) 个节点 · 线性插值 · 平滑调速" }
}
enum CurveMode: String, CaseIterable {
    case automatic, curve, rules
    var title: String {
        switch self { case .automatic: return "系统自动"; case .curve: return "自定义曲线"; case .rules: return "温度规则" }
    }
}
struct CurveDraft {
    var source = ""
    var mode: CurveMode = .automatic
    var fan = 0
    var sensorGroup = "cpu"
    var nodes: [CurveNode] = []
    var ramp = 200.0
    init() {}
    init(document: String, fans: [Fan]) throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let config = try decoder.decode(RulesConfiguration.self, from: Data(document.utf8))
        source = document; fan = config.fan
        mode = config.enabled ? (config.mode == "curve" ? .curve : .rules) : .automatic
        if let curve = config.curve {
            nodes = curve.nodes; sensorGroup = curve.sensorGroup; ramp = curve.rampRpmPerSecond ?? 200
        } else {
            let selected = fans.first { $0.id == fan }
            let low = Int(ceil(selected?.minRpm ?? 1000)), high = Int(floor(selected?.maxRpm ?? 4900))
            let span = max(0, high - low)
            nodes = [CurveNode(60, low), CurveNode(80, low + span / 4),
                     CurveNode(90, low + span * 3 / 5), CurveNode(100, high)]
        }
    }
    var sortedNodes: [CurveNode] { nodes.sorted { $0.temperatureCelsius < $1.temperatureCelsius } }
    mutating func addNode() -> UUID? {
        guard nodes.count < 16, nodes.count >= 2 else { return nil }
        let sorted = sortedNodes
        guard let pair = zip(sorted, sorted.dropFirst()).max(by: {
            $0.1.temperatureCelsius - $0.0.temperatureCelsius < $1.1.temperatureCelsius - $1.0.temperatureCelsius
        }), pair.1.temperatureCelsius - pair.0.temperatureCelsius >= 0.2 else { return nil }
        let temperature = ((pair.0.temperatureCelsius + pair.1.temperatureCelsius) * 5).rounded() / 10
        let fraction = (temperature - pair.0.temperatureCelsius) / (pair.1.temperatureCelsius - pair.0.temperatureCelsius)
        let node = CurveNode(temperature, Int((Double(pair.0.targetRpm) + fraction * Double(pair.1.targetRpm - pair.0.targetRpm)).rounded()))
        nodes.append(node); nodes.sort { $0.temperatureCelsius < $1.temperatureCelsius }
        return node.id
    }
    mutating func removeNode(_ id: UUID) {
        guard nodes.count > 2 else { return }
        nodes.removeAll { $0.id == id }
    }
    func document(fans: [Fan]) throws -> String {
        guard var object = try JSONSerialization.jsonObject(with: Data(source.utf8)) as? [String: Any] else {
            throw issue("请先读取当前配置。")
        }
        object["enabled"] = mode != .automatic
        if mode == .automatic { return try ruleDocument(object) }
        object["mode"] = mode.rawValue
        if mode == .rules { return try ruleDocument(object) }
        guard let selected = fans.first(where: { $0.id == fan }), selected.canControl else { throw issue("请选择可控的风扇。") }
        guard (2...16).contains(nodes.count) else { throw issue("请保留 2–16 个节点。") }
        guard ramp.isFinite && (1...1000).contains(ramp) else { throw issue("平滑速度需为每秒 1–1000 RPM。") }
        let sorted = sortedNodes
        for (index, node) in sorted.enumerated() {
            guard node.temperatureCelsius.isFinite && (-40...150).contains(node.temperatureCelsius) else { throw issue("节点温度需为 −40–150°C 内的有限数值。") }
            guard Double(node.targetRpm) >= selected.minRpm! && Double(node.targetRpm) <= selected.maxRpm! else {
                throw issue("每个节点转速需在 \(rpmText(selected.minRpm))–\(rpmText(selected.maxRpm)) RPM 内。")
            }
            if index > 0 {
                guard node.temperatureCelsius > sorted[index - 1].temperatureCelsius else { throw issue("节点温度不能重复。") }
                guard node.targetRpm >= sorted[index - 1].targetRpm else { throw issue("温度升高时转速应保持或增加。") }
            }
        }
        object["fan"] = fan
        object["curve"] = ["sensor_group": sensorGroup, "ramp_rpm_per_second": ramp,
                           "nodes": sorted.map { ["temperature_celsius": $0.temperatureCelsius, "target_rpm": $0.targetRpm] }]
        return try ruleDocument(object)
    }
    private func issue(_ text: String) -> NSError {
        NSError(domain: "AgenticFan", code: 2, userInfo: [NSLocalizedDescriptionKey: text])
    }
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
    let curveTargetRpm: Int?
    let activeRuleIds: [String]
    let error: String?
    let automaticModeVerified: Bool?
    let controlConfirmed: Bool
    let controlActive: Bool?
    let controlFeedback: ControlFeedback?
    let hook: RuleHookRuntime?
    var statusText: String {
        if status == "disabled" { return "系统自动" }
        if config?.mode == "curve", status == "active" { return "曲线平滑调速中" }
        return ["watching": "规则监测中", "pending": "高温持续确认中", "active": "温度规则已触发", "cooling": "降温确认中", "restoring": "正在恢复系统自动", "paused": "规则已暂停", "error": "规则运行异常"][status] ?? status
    }
    var mayOwnControl: Bool { controlActive == true || desiredRpm != nil || ["active", "cooling", "restoring"].contains(status) }
    var hookSummary: String {
        if config?.mode == "curve" { return "曲线模式 · 阈值触发代码已保留" }
        guard let program = config?.hook?.program else { return "未启用 · 编辑阈值触发时执行的代码" }
        let name = URL(fileURLWithPath: program).lastPathComponent
        if !enabled { return "\(name) · 温度规则已关闭" }
        return "\(name) · \(hook?.summary ?? "等待规则触发")"
    }
    var hookNeedsAttention: Bool {
        config?.hook != nil && ((hook?.dropped ?? 0) > 0 || ["failed", "timed_out"].contains(hook?.lastResult?.status ?? ""))
    }
}
struct ControlFeedback: Decodable {
    let status: String
    let targetRpm: Int?
    let actualRpm: Double?
    let sampledAt: String?
    let pendingUpdate: Bool

    func state(at now: Date) -> String {
        guard status == "tracking" || status == "adjusting" else { return status }
        guard let sampledAt else { return "stale" }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = formatter.date(from: sampledAt)
        if date == nil {
            formatter.formatOptions = [.withInternetDateTime]
            date = formatter.date(from: sampledAt)
        }
        guard let date, (0...3).contains(now.timeIntervalSince(date)) else { return "stale" }
        return status
    }

    func summary(at now: Date) -> String {
        let current = state(at: now)
        guard current == "tracking" || current == "adjusting", let targetRpm, let actualRpm, actualRpm.isFinite else {
            return ["waiting": "等待转速反馈", "stale": "转速反馈已过期", "stopped": "跟随监测已结束"][current] ?? "转速反馈不可用"
        }
        let label = current == "tracking" ? "跟随正常" : "正在调速"
        return "已下发 \(targetRpm) · 实际 \(rpmText(actualRpm)) RPM · \(label)" + (pendingUpdate ? " · 目标更新中" : "")
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
