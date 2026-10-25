import Foundation
import Combine
import ServiceManagement

enum LoginItemState: String {
    case notRegistered = "not_registered"
    case enabled
    case requiresApproval = "requires_approval"
    case notFound = "not_found"
}

func loginItemOperation(_ arguments: [String]) -> String? {
    let command = arguments.first == "--demo" ? Array(arguments.dropFirst()) : arguments
    guard command.count == 2, command[0] == "--login-item",
          ["status", "enable", "disable"].contains(command[1]) else { return nil }
    return command[1]
}

protocol LoginItemService {
    var state: LoginItemState { get }
    func register() throws
    func unregister() throws
}

private struct SystemLoginItem: LoginItemService {
    var state: LoginItemState {
        switch SMAppService.mainApp.status {
        case .notRegistered: return .notRegistered
        case .enabled: return .enabled
        case .requiresApproval: return .requiresApproval
        case .notFound: return .notFound
        @unknown default: return .notFound
        }
    }
    func register() throws { try SMAppService.mainApp.register() }
    func unregister() throws { try SMAppService.mainApp.unregister() }
}

protocol LoginItemPreferences: AnyObject {
    var defaultApplied: Bool { get set }
}

private final class SystemLoginItemPreferences: LoginItemPreferences {
    var defaultApplied: Bool {
        get { UserDefaults.standard.bool(forKey: "loginItemDefaultApplied") }
        set { UserDefaults.standard.set(newValue, forKey: "loginItemDefaultApplied") }
    }
}

final class LoginItemSettings: ObservableObject {
    static let shared = LoginItemSettings(service: SystemLoginItem(), preferences: SystemLoginItemPreferences(),
                                          demo: CommandLine.arguments.contains("--demo"))
    @Published private(set) var state = LoginItemState.notRegistered
    @Published private(set) var error: String?
    private let service: LoginItemService
    private let preferences: LoginItemPreferences
    private let demo: Bool
    private var defaultAttempted = false

    init(service: LoginItemService, preferences: LoginItemPreferences, demo: Bool) {
        self.service = service; self.preferences = preferences; self.demo = demo
    }

    var enabled: Bool { state == .enabled }
    var statusText: String {
        if demo { return "演示模式，不修改登录项。" }
        switch state {
        case .enabled: return "已开启：登录后在菜单栏恢复监测，并载入已保存的温度规则和自定义 Hook。"
        case .notRegistered: return "已关闭登录时自动启动。"
        case .requiresApproval: return "登录项已被系统关闭，请在系统设置中允许。"
        case .notFound: return "尚未设置登录自启。开启后，登录时会自动恢复监测、温度规则和自定义 Hook。"
        }
    }

    func configureDefault() {
        guard !demo else { return }
        refresh()
        guard !preferences.defaultApplied, !defaultAttempted else { return }
        defaultAttempted = true
        // Failed registrations retry on the next launch, not on every refresh.
        // Successful defaults and deliberate opt-outs remain persistent.
        // A main app without an existing BTM record can also report notFound.
        if state == .notRegistered || state == .notFound { setEnabled(true) }
        else { preferences.defaultApplied = true }
    }

    func refresh() {
        guard !demo else { return }
        state = service.state
    }

    func setEnabled(_ enabled: Bool) {
        guard !demo else { return }
        if !enabled { preferences.defaultApplied = true }
        error = nil
        refresh()
        if enabled && state == .enabled { preferences.defaultApplied = true; return }
        if !enabled && (state == .notRegistered || state == .notFound) { return }
        do {
            if enabled { try service.register() }
            else { try service.unregister() }
        } catch {
            let detail = error as NSError
            self.error = "登录项更新失败：\(detail.localizedDescription)（\(detail.domain) \(detail.code)）"
        }
        refresh()
        if enabled && (state == .enabled || state == .requiresApproval) {
            preferences.defaultApplied = true
        }
    }

    func command(_ operation: String) -> Bool {
        switch operation {
        case "status": refresh()
        case "enable": setEnabled(true)
        case "disable": setEnabled(false)
        default: return false
        }
        let report: [String: Any] = ["state": state.rawValue, "enabled": enabled,
                                     "simulated": demo, "error": error as Any? ?? NSNull()]
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]),
           let text = String(data: data, encoding: .utf8) { print(text) }
        return true
    }

    func openSystemSettings() {
        guard !demo else { return }
        SMAppService.openSystemSettingsLoginItems()
    }
}
