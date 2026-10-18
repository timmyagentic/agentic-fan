import Foundation

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
        throw NSError(domain: "MacFan", code: 2, userInfo: [NSLocalizedDescriptionKey: "请选择范围内的转速与 1–3600 秒的持续时间。"])
    }
    return ["fans", "set", "--fan", String(fan.id), "--rpm", String(rpm), "--duration", String(duration)]
}
