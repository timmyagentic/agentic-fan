import AppKit
import SwiftUI

struct HookEntry: View {
    @ObservedObject var model: Monitor
    var body: some View {
        Button {
            AppDelegate.instance?.showMain()
            model.showHooks = true
        } label: {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 5) {
                    Label("阈值 Hook", systemImage: "curlybraces")
                        .font(.caption.weight(.semibold))
                    Text(model.snapshot?.rules?.hookSummary ?? "正在读取 Hook 配置…")
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(2).multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
        }.buttonStyle(.plain)
            .foregroundStyle(model.snapshot?.rules?.hookNeedsAttention == true ? Color.orange : .primary)
            .accessibilityLabel("阈值 Hook，\(model.snapshot?.rules?.hookSummary ?? "正在读取")，打开设置")
    }
}

final class HookEditorModel: ObservableObject {
    @Published var document: HookDocument?
    @Published var statusDocument: HookDocument?
    @Published var content = ""
    @Published var enabled = false
    @Published var timeout = 10.0
    @Published var busy = false
    @Published var loaded = false
    @Published var issue: String?
    @Published var notice: String?
    @Published var validation: HookValidation?
    @Published var example = ""
    var useDefault = false
    private var command: Command?
    private let decoder: JSONDecoder = { let value = JSONDecoder(); value.keyDecodingStrategy = .convertFromSnakeCase; return value }()
    var edited: Bool { document.map { content != ($0.script.content ?? "") } ?? false }
    private var selection: [String] { useDefault ? ["--default"] : [] }

    private func decode<T: Decodable>(_ type: T.Type, _ object: Any) throws -> T {
        try decoder.decode(type, from: JSONSerialization.data(withJSONObject: object))
    }
    private func accept(_ data: [String: Any]) throws {
        let value = try decode(HookDocument.self, data)
        document = value; statusDocument = value
        content = value.script.content ?? ""
        enabled = value.settings.enabled; timeout = value.settings.timeoutSeconds
        if let event = data["event_example"] { example = try ruleDocument(event) }
        loaded = true
    }
    func load(_ model: Monitor) {
        guard !busy else { return }
        loaded = false; validation = nil
        request(model.hookArguments("show", selection)) { [weak self] data in try self?.accept(data) }
    }
    func check(_ model: Monitor) {
        guard !busy, loaded, document?.script.editable == true else { return }
        request(model.hookArguments("validate", selection + ["--stdin"]), input: content) { [weak self] data in
            guard let self else { return }
            validation = try decode(HookValidation.self, data)
        }
    }
    func refreshStatus(_ model: Monitor) {
        guard !busy else { return }
        request(model.hookArguments("status", selection)) { [weak self] data in
            guard let self else { return }
            // Status refresh must not silently rebase an unsaved draft's revisions.
            statusDocument = try decode(HookDocument.self, data)
        }
    }
    func save(_ model: Monitor) {
        guard !busy, loaded, let document else { return }
        do {
            let writing = document.script.editable && (enabled || edited || !document.script.exists)
            let args = try document.applyOptions(enabled: enabled, timeout: timeout, useDefault: useDefault, includeContent: writing)
            model.hookSaving = true
            request(model.hookArguments("apply", args), input: writing ? content : nil,
                    finished: { model.hookSaving = false }) { [weak self] data in
                guard let self, let updated = data["document"] as? [String: Any] else { return }
                try accept(updated)
                if let checked = data["validation"] as? [String: Any] { validation = try decode(HookValidation.self, checked) }
                notice = "已保存；下次规则触发时读取脚本。"
                if data["settings_saved"] as? Bool == true && data["rules_loaded"] as? Bool != true {
                    notice = "已保存；监测进程尚未确认载入设置。"
                }
                if let revision = data["script_saved_revision"] as? String, revision != self.document?.script.revision {
                    notice = "脚本已保存，但文件随后又有修改，请重新读取并核对。"
                }
            }
        } catch { issue = error.localizedDescription }
    }
    private func request(_ arguments: [String], input: String? = nil, finished: @escaping () -> Void = {},
                         receive: @escaping ([String: Any]) throws -> Void) {
        guard !busy else { return }
        busy = true; issue = nil; notice = nil
        // A 256 KiB script can expand to 1.5 MiB when JSON escapes control characters.
        let task = Command(arguments, input: input, maximumFrameBytes: 2 * 1_048_576)
        command = task
        var received = false
        do {
            try task.run(event: { [weak self] message in
                guard let self else { return }
                if let error = message["error"] as? [String: Any] {
                    let code = error["code"] as? String ?? ""
                    let details = error["details"] as? [String: Any] ?? [:]
                    if let checked = details["validation"] as? [String: Any],
                       let report = try? decode(HookValidation.self, checked) { validation = report }
                    let labels = ["HOOK_CONFLICT": "脚本已被外部修改，请重新读取后再保存。",
                                  "CONFIG_CONFLICT": "规则设置已被外部修改，请重新读取后再保存。",
                                  "HOOK_BUSY": "另一个客户端正在保存，请稍后重新读取。",
                                  "HOOK_VALIDATION_FAILED": "脚本检查未通过，文件尚未保存。"]
                    issue = labels[code] ?? error["message"] as? String ?? "Hook 操作未完成。"
                    if code == "HOOK_APPLY_PARTIAL" {
                        issue = (details["script_saved"] as? Bool == true ? "脚本已保存；" : "") + "操作尚未完整完成，请重新读取并核对设置。"
                    }
                    return
                }
                guard let data = message["data"] as? [String: Any], message["ok"] as? Bool == true else { return }
                do { try receive(data); received = true }
                catch { issue = "无法读取 Hook 回执：\(error.localizedDescription)" }
            }, end: { [weak self] code in
                guard let self else { finished(); return }
                busy = false; command = nil
                if (code != 0 || !received) && issue == nil { issue = "Hook 操作未完成（\(code)）。" }
                finished()
            })
        } catch { busy = false; command = nil; issue = error.localizedDescription; finished() }
    }
}

struct HookSheet: View {
    @ObservedObject var model: Monitor
    @StateObject private var editor = HookEditorModel()
    @Environment(\.dismiss) private var dismiss
    private var locked: Bool { editor.busy || model.busy || model.rulesSaving || model.rulesStopping || model.restoring }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("阈值 Hook", systemImage: "curlybraces").font(.headline)
            Text("温度规则达到持续触发条件后，运行这个文件中的代码。直接编辑并保存，可以写文件、调用 API 或运行其他工具。")
                .font(.callout).fixedSize(horizontal: false, vertical: true)
            HStack {
                Toggle("启用 Hook", isOn: $editor.enabled)
                Spacer()
                Text("超时")
                TextField("秒", value: $editor.timeout, format: .number)
                    .textFieldStyle(.roundedBorder).frame(width: 60).accessibilityLabel("Hook 超时时间（秒）")
                Text("秒").foregroundStyle(.secondary)
            }.disabled(locked || !editor.loaded)
            if let script = editor.document?.script, script.editable {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Label(URL(fileURLWithPath: script.path).lastPathComponent, systemImage: "doc.text").font(.caption.weight(.medium))
                        Text(editor.edited ? "未保存" : (script.exists ? "已保存" : "新脚本"))
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button("打开所在位置") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: script.path)]) }
                            .font(.caption).disabled(!script.exists)
                    }
                    HookCodeEditor(text: $editor.content).frame(height: 200).padding(8)
                        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                        .accessibilityLabel("Hook 脚本代码").disabled(locked || !editor.loaded)
                    HStack {
                        Text("默认不执行任何动作，保存后下次触发读取最新内容。").foregroundStyle(.secondary)
                        Spacer()
                        Button("检查草稿") { editor.check(model) }.disabled(locked || !editor.loaded)
                    }.font(.caption)
                }
            } else if let problem = editor.document?.script.issue {
                Text(problem.message).font(.callout).foregroundStyle(.orange).lineLimit(3)
                Button("使用默认脚本") { editor.useDefault = true; editor.load(model) }.disabled(locked || !editor.loaded)
                Text("保存设置后才会切换，原文件会保留。").font(.caption).foregroundStyle(.secondary)
            }
            if let validation = editor.validation {
                Text(validation.summary).font(.caption).foregroundStyle(validation.valid ? Color.secondary : .orange)
                    .lineLimit(3).textSelection(.enabled).help(validation.summary)
            }
            DisclosureGroup("查看事件数据") {
                VStack(alignment: .leading, spacing: 7) {
                    Text("从标准输入读取一行 JSON。每条规则每次激活运行一次，执行失败不影响控扇；超时范围 0.1–60 秒。")
                        .font(.caption).foregroundStyle(.secondary)
                    ScrollView { Text(editor.example).font(.system(size: 11, design: .monospaced))
                        .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(8) }.frame(height: 90)
                    Link("查看协议与 Agent 用法", destination: URL(string: "https://github.com/timmyagentic/agentic-fan/blob/codex/apple-silicon-dmg/docs/temperature-rules.md#固定程序-hook")!)
                }.padding(.top, 8)
            }.font(.caption)
            Divider()
            HStack {
                Text(editor.statusDocument?.executionSummary ?? "正在读取…").font(.caption)
                Spacer()
                Button("刷新状态") { editor.refreshStatus(model) }.font(.caption).disabled(locked || !editor.loaded)
            }
            if let state = editor.statusDocument {
                Text(state.revisionSummary).font(.caption).foregroundStyle(.secondary)
                if let validation = state.validation {
                    Text("已保存文件：" + validation.summary).font(.caption)
                        .foregroundStyle(validation.valid ? Color.secondary : .orange).lineLimit(2).help(validation.summary)
                }
            }
            Text("代码以你的账号运行。检查不会执行代码；执行成功只表示程序正常退出。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let error = editor.issue {
                Text(error).font(.caption).foregroundStyle(.orange).lineLimit(3).textSelection(.enabled).help(error)
            } else if let notice = editor.notice { Text(notice).font(.caption).foregroundStyle(.secondary) }
            HStack {
                Button("重新读取") { editor.load(model) }.disabled(locked)
                if editor.busy { ProgressView().controlSize(.small) }
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction).disabled(editor.busy)
                Button("保存脚本与设置") { editor.save(model) }.buttonStyle(.borderedProminent).keyboardShortcut("s")
                    .disabled(locked || !editor.loaded || !model.online || (editor.enabled && editor.document?.script.editable != true))
            }
        }.padding(22).frame(width: 590).onAppear { editor.load(model) }
    }
}
