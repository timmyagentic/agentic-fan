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
    @State private var loaded = false
    @State private var issue: String?
    @State private var example = 0
    private var locked: Bool { model.rulesLoading || model.rulesSaving || model.busy || model.rulesStopping }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("阈值 Hook", systemImage: "curlybraces").font(.headline)
            Text("温度规则达到持续触发条件后，运行你设定的代码。可以写文件、调用 API、启动工具或执行其他自定义动作。")
                .font(.callout).fixedSize(horizontal: false, vertical: true)
            Toggle("启用 Hook", isOn: $draft.enabled)
                .disabled(locked || !loaded)
            if draft.enabled {
                VStack(alignment: .leading, spacing: 7) {
                    Text("程序或脚本").font(.caption.weight(.medium))
                    HStack {
                        TextField("本机可执行文件或脚本的完整路径", text: $draft.program)
                            .textFieldStyle(.roundedBorder).accessibilityLabel("Hook 程序路径")
                        Button("选择程序…") { chooseProgram() }
                    }
                    HStack {
                        Text("超时时间")
                        TextField("秒", value: $draft.timeoutSeconds, format: .number)
                            .textFieldStyle(.roundedBorder).frame(width: 70).accessibilityLabel("Hook 超时时间（秒）")
                        Text("秒（0.1–60）").foregroundStyle(.secondary)
                    }.font(.caption)
                }.disabled(locked || !loaded)
            }
            Text("支持 Shell、Python、JavaScript 等可执行脚本和编译后的程序。代码以你的账号运行，请配置好解释器和执行权限，只运行你信任的代码。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            DisclosureGroup("事件数据与代码示例") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("程序从标准输入接收一行 JSON，包括触发规则、温度和控扇状态。每条规则每次激活运行一次，失败不影响控扇。")
                        .font(.caption).foregroundStyle(.secondary)
                    Picker("Hook 示例", selection: $example) {
                        Text("事件 JSON").tag(0)
                        Text("Python").tag(1)
                        Text("Shell").tag(2)
                    }.pickerStyle(.segmented)
                    ScrollView {
                        Text(RuleHookExamples.samples[example]).font(.system(size: 11, design: .monospaced))
                            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(8)
                    }.frame(height: 135).background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                    Button("复制示例") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(RuleHookExamples.samples[example], forType: .string)
                    }.font(.caption)
                }.padding(.top, 8)
            }.font(.caption)
            Link("查看 Hook 协议与更多用法", destination: URL(string: "https://github.com/timmyagentic/agentic-fan/blob/codex/apple-silicon-dmg/docs/temperature-rules.md#固定程序-hook")!)
                .font(.caption)
            Divider()
            VStack(alignment: .leading, spacing: 5) {
                Text("当前状态").font(.caption.weight(.semibold))
                Text(model.snapshot?.rules?.hookSummary ?? "正在读取…").font(.caption)
                if model.snapshot?.rules?.config?.hook != nil {
                    Text("已配置表示程序路径已保存；执行成功表示程序正常退出，具体动作的结果由代码自己确认。")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Text("登录自启会恢复已保存的规则和 Hook。程序输出不会保存在界面中，需要的日志请由代码写入文件。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if model.rulesChangedExternally {
                Text("配置已从外部更新，请重新读取后再保存。").font(.caption).foregroundStyle(.orange)
            }
            if let error = issue ?? model.rulesEditorError ?? model.error {
                Text(error).font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            } else if let notice = model.rulesNotice {
                Text(notice).font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button("重新读取") { reload() }.disabled(locked)
                if locked { ProgressView().controlSize(.small) }
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction).disabled(model.rulesSaving)
                Button("保存并应用") { save() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(locked || !loaded || model.restoring || model.rulesChangedExternally || !model.online)
            }
        }.padding(22).frame(width: 510)
            .onAppear { reload() }
            .onChange(of: model.rulesLoading) { _, loading in
                guard !loading, model.rulesEditorError == nil else { return }
                do { draft = try RuleHookDraft(document: model.rulesDraft); loaded = true; issue = nil }
                catch { loaded = false; issue = error.localizedDescription }
            }
    }
    private func reload() { loaded = false; issue = nil; model.reloadRules() }
    private func chooseProgram() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.title = "选择 Hook 程序"; panel.prompt = "使用此程序"
        panel.message = "选择你信任的本机可执行程序或脚本。"
        if panel.runModal() == .OK, let path = panel.url?.path { draft.program = path; issue = nil }
    }
    private func save() {
        do {
            let document = try draft.applying(to: model.rulesDraft)
            if draft.enabled {
                var directory: ObjCBool = false
                guard FileManager.default.fileExists(atPath: draft.program, isDirectory: &directory),
                      !directory.boolValue, FileManager.default.isExecutableFile(atPath: draft.program) else {
                    issue = "这个文件不存在或没有执行权限，请选择可执行程序。"; return
                }
            }
            issue = nil; model.rulesDraft = document; model.saveRules()
        } catch { issue = error.localizedDescription }
    }
}
