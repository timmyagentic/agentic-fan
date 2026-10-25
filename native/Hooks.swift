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

struct HookSheet: View {
    @ObservedObject var model: Monitor
    @Environment(\.dismiss) private var dismiss
    @State private var draft = RuleHookDraft()
    @State private var script: HookScript?
    @State private var code = ""
    @State private var loaded = false
    @State private var issue: String?
    @State private var fileIssue: String?
    @State private var scriptSaved = false
    private var locked: Bool { model.rulesLoading || model.rulesSaving || model.busy || model.rulesStopping }
    private var edited: Bool { script.map { code != $0.contents } ?? false }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("阈值 Hook", systemImage: "curlybraces").font(.headline)
            Text("温度规则达到持续触发条件后，运行这个文件中的代码。直接编辑并保存，可以写文件、调用 API 或运行其他工具。")
                .font(.callout).fixedSize(horizontal: false, vertical: true)
            HStack {
                Toggle("启用 Hook", isOn: $draft.enabled)
                Spacer()
                Text("超时")
                TextField("秒", value: $draft.timeoutSeconds, format: .number)
                    .textFieldStyle(.roundedBorder).frame(width: 60).accessibilityLabel("Hook 超时时间（秒）")
                Text("秒").foregroundStyle(.secondary)
            }.disabled(locked || !loaded)
            if let script {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Label(script.url.lastPathComponent, systemImage: "doc.text").font(.caption.weight(.medium))
                        Text(edited ? "未保存" : (script.exists ? "已保存" : "新脚本"))
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button("打开所在位置") { NSWorkspace.shared.activateFileViewerSelecting([script.url]) }
                            .font(.caption).disabled(!script.exists)
                    }
                    HookCodeEditor(text: $code)
                        .frame(height: 220)
                        .padding(8)
                        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                        .accessibilityLabel("Hook 脚本代码")
                        .disabled(locked || !loaded)
                    Text("默认脚本不执行任何动作。保存后，下次触发会运行最新代码；也可以在外部编辑这个文件。")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            } else if let fileIssue {
                Text(fileIssue).font(.callout).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                Button("使用默认脚本") { loadDefaultScript() }.disabled(locked || !loaded)
                Text("保存设置后才会切换，原文件会保留。").font(.caption).foregroundStyle(.secondary)
            }
            DisclosureGroup("查看事件数据") {
                VStack(alignment: .leading, spacing: 7) {
                    Text("从标准输入读取一行 JSON。每条规则每次激活运行一次，执行失败不影响控扇；超时范围 0.1–60 秒。")
                        .font(.caption).foregroundStyle(.secondary)
                    ScrollView {
                        Text(RuleHookExamples.event).font(.system(size: 11, design: .monospaced))
                            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(8)
                    }.frame(height: 100)
                    Link("查看协议与代码示例", destination: URL(string: "https://github.com/timmyagentic/agentic-fan/blob/codex/apple-silicon-dmg/docs/temperature-rules.md#固定程序-hook")!)
                }.padding(.top, 8)
            }.font(.caption)
            Divider()
            Text(model.snapshot?.rules?.hookSummary ?? "正在读取…").font(.caption)
            Text("代码以你的账号运行。执行成功表示程序正常退出；详细日志由代码写入自己的文件。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if model.rulesChangedExternally {
                Text("配置已从外部更新，请重新读取后再保存。").font(.caption).foregroundStyle(.orange)
            }
            if let error = issue ?? model.rulesEditorError ?? model.error {
                Text((scriptSaved ? "脚本已保存；设置尚未应用。" : "") + error)
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            } else if let notice = model.rulesNotice {
                Text((scriptSaved ? "脚本已保存。" : "") + notice).font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button("重新读取") { reload() }.disabled(locked)
                if locked { ProgressView().controlSize(.small) }
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction).disabled(model.rulesSaving)
                Button("保存脚本与设置") { save() }.buttonStyle(.borderedProminent).keyboardShortcut("s")
                    .disabled(locked || !loaded || model.restoring || model.rulesChangedExternally || !model.online || (draft.enabled && script == nil))
            }
        }.padding(22).frame(width: 590)
            .onAppear { reload() }
            .onChange(of: model.rulesLoading) { _, loading in
                guard !loading, model.rulesEditorError == nil else { return }
                do {
                    draft = try RuleHookDraft(document: model.rulesDraft)
                    loaded = true; issue = nil; scriptSaved = false
                    if draft.program.isEmpty { loadDefaultScript() }
                    else { loadScript(URL(fileURLWithPath: draft.program), allowNew: false) }
                } catch { loaded = false; issue = error.localizedDescription }
            }
    }
    private func reload() { loaded = false; issue = nil; scriptSaved = false; model.reloadRules() }
    private func loadDefaultScript() {
        guard !model.rulesPath.isEmpty else { issue = "尚未读到规则文件位置，请重新读取。"; return }
        loadScript(HookScript.defaultURL(rulesPath: model.rulesPath), allowNew: true)
    }
    private func loadScript(_ url: URL, allowNew: Bool) {
        do {
            let opened = try HookScript(url: url, allowNew: allowNew)
            script = opened; code = opened.contents; draft.program = url.path; fileIssue = nil; issue = nil
        } catch { script = nil; code = ""; fileIssue = error.localizedDescription }
    }
    private func save() {
        do {
            let document = try draft.applying(to: model.rulesDraft)
            scriptSaved = false
            if var file = script {
                // A missing/unreadable old program must never prevent turning its Hook off.
                if draft.enabled || code != file.contents || !file.exists {
                    try file.save(code)
                    script = file; code = file.contents; scriptSaved = true
                }
            }
            issue = nil; model.rulesDraft = document; model.saveRules()
        } catch { issue = error.localizedDescription }
    }
}
