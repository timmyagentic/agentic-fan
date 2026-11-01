import Foundation
import ServiceManagement
import AppKit

// Called in the main bundle context so ServiceManagement can resolve BundleProgram.
// These commands exit before app startup, login registration or monitoring.
enum ControlService {
    static let plist = "io.github.timmyagentic.agenticfan.control.plist"

    static func command(_ operation: String) -> Int32 {
        let app = Bundle.main.bundleURL.standardizedFileURL
        let allowed = [URL(fileURLWithPath: "/Applications"),
                       FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications")]
        let installed = allowed.contains { $0.appendingPathComponent("AgenticFan.app").standardizedFileURL.path == app.path }
        func finish(_ state: String, _ message: String? = nil) -> Int32 {
            var report = ["state": state]
            if let message { report["message"] = message }
            if let bytes = try? JSONSerialization.data(withJSONObject: report) { print(String(decoding: bytes, as: UTF8.self)) }
            return message == nil ? 0 : 1
        }
        guard installed && Bundle.main.bundleIdentifier == "io.github.timmyagentic.agenticfan" else {
            return operation == "status" ? finish("not_installed") : finish("not_installed", "请先将 AgenticFan 安装到应用程序目录。")
        }
        let installedCopies = allowed.map { $0.appendingPathComponent("AgenticFan.app") }
            .filter { FileManager.default.fileExists(atPath: $0.path) }
        if Set(installedCopies.map { $0.resolvingSymlinksInPath().path }).count > 1 {
            return finish("multiple_installations")
        }
        if Bundle.main.object(forInfoDictionaryKey: "AgenticFanDevelopmentService") as? Bool == true {
            return finish("development_build") // SMAppService daemons require a notarized containing app.
        }
        let service = SMAppService.daemon(plistName: plist)
        do {
            switch operation {
            case "status": break
            case "register":
                if service.status != .enabled && service.status != .requiresApproval { try service.register() }
            case "unregister":
                if service.status == .enabled || service.status == .requiresApproval {
                    // The synchronous unregister returns before launchd reaps its process.
                    // Only the completion-handler variant is safe to re-register after.
                    let done = DispatchSemaphore(value: 0)
                    var failure: Error?
                    service.unregister { error in failure = error; done.signal() }
                    guard done.wait(timeout: .now() + 20) == .success else {
                        return finish("error", "等待旧服务停止超时，应用尚未替换。")
                    }
                    if let failure { throw failure }
                }
            case "settings": SMAppService.openSystemSettingsLoginItems()
            default: return finish("invalid", "未知服务操作。")
            }
        } catch {
            if service.status == .requiresApproval && operation == "register" { return finish("requires_approval") }
            return finish("error", error.localizedDescription)
        }
        switch service.status {
        case .enabled: return finish("enabled")
        case .requiresApproval: return finish("requires_approval")
        case .notRegistered: return finish("not_registered")
        case .notFound: return finish("not_found")
        @unknown default: return finish("unknown", "无法读取后台服务状态。")
        }
    }

    static func canReplace(_ destination: String) -> Bool {
        let path = URL(fileURLWithPath: destination).resolvingSymlinksInPath().path
        return !NSRunningApplication.runningApplications(withBundleIdentifier: "io.github.timmyagentic.agenticfan").contains {
            $0.processIdentifier != ProcessInfo.processInfo.processIdentifier && $0.bundleURL?.resolvingSymlinksInPath().path == path
        }
    }
}
