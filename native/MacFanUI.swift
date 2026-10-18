import AppKit
import SwiftUI
import Combine
import Darwin

final class Monitor: ObservableObject {
    static let shared = Monitor()
    @Published var snapshot: Snapshot?
    @Published var error: String?
    @Published var status = "正在连接…"
    @Published var helperReady = false
    @Published var helperStatus = "正在检查后台服务…"
    @Published var busy = false
    @Published var restoring = false
    @Published var receipt = ControlReceipt()
    @Published var activeFanID: Int?
    @Published var selectedFan: Fan?
    @Published var showSettings = false
    @Published var showDetails = false
    @Published var search = ""
    let demo = CommandLine.arguments.contains("--demo")
    let smoke = CommandLine.arguments.contains("--smoke")
    let decoder: JSONDecoder = { let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase; return d }()
    private var watch: Command?
    private var control: Command?
    private var utility: Command?
    private var smokeStarted = false
    private var quitting = false
    var online: Bool { snapshot != nil }
    var components: [Component] { snapshot?.temperatureOverview ?? [] }
    var fans: [Fan] { snapshot?.fans ?? [] }
    var prefix: [String] { demo ? ["--demo"] : [] }
    var machine: String {
        if demo { return "演示设备 · 模拟数据" }
        var size = 0
        sysctlbyname("machdep.cpu.brand_string", nil, &size, nil, 0)
        var bytes = [CChar](repeating: 0, count: max(size, 1))
        if sysctlbyname("machdep.cpu.brand_string", &bytes, &size, nil, 0) == 0 { return "Mac · " + String(cString: bytes) }
        return "当前 Mac"
    }
    func start() {
        guard watch == nil else { return }
        error = nil
        let task = Command(prefix + ["watch", "--interval", "1"])
        watch = task
        do {
            try task.run(event: { [weak self] message in
                guard let self, self.watch === task else { return }
                guard message["ok"] as? Bool == true, let data = message["data"],
                      let bytes = try? JSONSerialization.data(withJSONObject: data),
                      let sample = try? decoder.decode(Snapshot.self, from: bytes) else {
                    snapshot = nil
                    error = (message["error"] as? [String: Any])?["message"] as? String ?? "监测数据无法读取。"
                    status = "连接中断"
                    return
                }
                snapshot = sample
                status = demo ? "演示模式" : "已连接"
                if smoke && !smokeStarted, let fan = sample.fans.first {
                    smokeStarted = true
                    set(fan, rpm: 3000, duration: 1)
                }
            }, end: { [weak self] code in
                guard let self, !quitting, self.watch === task else { return }
                watch = nil; snapshot = nil; status = "连接中断"
                error = error ?? "监测已停止（\(code)）。请重试连接。"
            })
        } catch { watch = nil; self.error = error.localizedDescription; status = "连接失败" }
        refreshHelper()
        if smoke {
            DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
                if let self, !quitting { finishSmoke(false) }
            }
        }
    }
    func refreshHelper() {
        if demo { helperReady = true; helperStatus = "演示模式，不访问系统 Helper"; return }
        let task = Command(["helper", "status"])
        utility = task
        do {
            try task.run(event: { [weak self] message in
                guard let self else { return }
                let data = message["data"] as? [String: Any] ?? [:]
                helperReady = data["running"] as? Bool == true && data["compatible"] as? Bool == true
                helperStatus = helperReady ? "后台服务已连接 · \(data["version"] as? String ?? "")" : "控扇服务需要安装或更新"
            }, end: { _ in })
        } catch { helperReady = false; helperStatus = error.localizedDescription }
    }
    func installHelper(then: (() -> Void)? = nil) {
        guard !busy else { return }
        busy = true; error = nil; helperStatus = "等待系统认证…"
        let task = Command(["helper", "install"])
        utility = task
        var ready = false
        do {
            try task.run(event: { [weak self] message in
                let data = message["data"] as? [String: Any] ?? [:]
                ready = data["running"] as? Bool == true && data["compatible"] as? Bool == true
                if let detail = message["error"] as? [String: Any] { self?.error = detail["message"] as? String }
            }, end: { [weak self] code in
                guard let self else { return }
                busy = false; helperReady = code == 0 && ready
                helperStatus = helperReady ? "后台服务已连接" : "安装未完成，可重试"
                if helperReady { then?() } else { error = error ?? "后台服务安装未完成。" }
            })
        } catch { busy = false; self.error = error.localizedDescription }
    }
    func set(_ fan: Fan, rpm: Int, duration: Int) {
        guard !busy, !receipt.recoveryRequired, online else { return }
        guard helperReady || demo else {
            installHelper { [weak self] in self?.set(fan, rpm: rpm, duration: duration) }; return
        }
        do {
            let arguments = try controlArguments(fan: fan, rpm: rpm, duration: duration)
            busy = true; error = nil; receipt = ControlReceipt(); selectedFan = nil; activeFanID = fan.id
            let task = Command(prefix + arguments)
            control = task
            try task.run(event: { [weak self] message in self?.receipt.receive(message) }, end: { [weak self] code in
                guard let self else { return }
                control = nil; busy = false
                error = receipt.completionError(exitCode: code)
                if code == 7 { receipt.recoveryRequired = true }
                if !receipt.recoveryRequired { activeFanID = nil }
                if smoke { finishSmoke(error == nil && receipt.confirmed) }
            })
        } catch { busy = false; self.error = error.localizedDescription }
    }
    func restore(fanID: Int? = nil, completion: ((Bool) -> Void)? = nil) {
        guard !restoring else { return }
        restoring = true; error = nil
        let task = Command(prefix + ["fans", "auto", "--fan", fanID.map(String.init) ?? "all"])
        utility = task
        var response = ControlReceipt()
        do {
            try task.run(event: { response.receive($0) }, end: { [weak self] code in
                guard let self else { return }
                restoring = false
                error = response.completionError(exitCode: code)
                if error == nil && (fanID == nil || fanID == activeFanID) { receipt.restored = true; receipt.recoveryRequired = false; activeFanID = nil }
                completion?(error == nil)
            })
        } catch { restoring = false; self.error = error.localizedDescription; completion?(false) }
    }
    func retry() { watch?.stop(); watch = nil; start() }
    func shutdown() { quitting = true; watch?.stop(); control?.stop(); utility?.stop() }
    private func finishSmoke(_ ok: Bool) {
        shutdown()
        print(ok ? "MACFAN_NATIVE_SMOKE_OK" : "MACFAN_NATIVE_SMOKE_FAILED")
        fflush(stdout)
        exit(ok ? 0 : 1)
    }
}

struct SummaryStrip: View {
    @ObservedObject var model: Monitor
    var body: some View {
        HStack(spacing: 20) {
            ForEach(["cpu", "gpu", "storage"], id: \.self) { group in
                let row = model.components.first { $0.group == group }
                HStack(spacing: 7) {
                    Image(systemName: row?.symbol ?? "thermometer.medium").foregroundStyle(.secondary)
                    Text(["cpu": "CPU 最高", "gpu": "GPU 最高", "storage": "SSD"][group]!).foregroundStyle(.secondary)
                    Text(temperature(row?.value, precise: false)).fontWeight(.semibold).monospacedDigit()
                }
                if group != "storage" { Divider().frame(height: 16) }
            }
            Spacer(minLength: 0)
        }.font(.system(size: 12)).padding(.horizontal, 18).frame(height: 44)
    }
}

struct FanPanel: View {
    @ObservedObject var model: Monitor
    var compact = false
    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 10 : 14) {
            if !compact { Text("风扇").font(.headline) }
            if model.fans.isEmpty {
                Text(model.online ? "未检测到风扇" : "正在读取风扇…").foregroundStyle(.secondary)
            }
            ForEach(model.fans) { fan in
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 10) {
                        Image(systemName: "fanblades.fill").font(.system(size: compact ? 26 : 32)).foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(fan.title).font(.system(size: 12, weight: .medium))
                            HStack(alignment: .firstTextBaseline, spacing: 4) {
                                Text(rpmText(fan.actualRpm)).font(.system(size: compact ? 20 : 25, weight: .semibold)).monospacedDigit()
                                Text("RPM").font(.system(size: 12)).foregroundStyle(.secondary)
                            }
                            if !compact { Text("\(rpmText(fan.minRpm))–\(rpmText(fan.maxRpm)) RPM").font(.caption).foregroundStyle(.secondary) }
                        }
                        Spacer(minLength: 0)
                        if compact { Text(mode(fan)).font(.caption).foregroundStyle(fan.mode == "manual" ? Color.accentColor : .secondary) }
                    }
                    if !compact {
                        Picker("风扇模式", selection: Binding<String>(
                            get: { fan.mode },
                            set: { mode in
                                if mode == "auto" { model.restore(fanID: fan.id) }
                                else if !model.busy && !model.receipt.recoveryRequired { model.selectedFan = fan }
                            })) {
                            Text("自动").tag("auto")
                            Text("自定义…").tag("manual")
                        }.pickerStyle(.segmented).labelsHidden().disabled(model.restoring || !fan.canControl)
                        if model.busy && model.activeFanID == fan.id && model.receipt.started && !model.receipt.restored {
                            TimelineView(.periodic(from: .now, by: 1)) { context in
                                let remaining = max(0, Int((model.receipt.deadline ?? context.date).timeIntervalSince(context.date)))
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("目标 \(model.receipt.target ?? 0) RPM · 剩余 \(remaining / 60):\(String(format: "%02d", remaining % 60))").monospacedDigit()
                                    Text(model.receipt.confirmed ? "已达到目标转速" : "正在达到目标转速…").foregroundStyle(.secondary)
                                }.font(.caption)
                            }
                        } else {
                            Text(mode(fan)).font(.caption).foregroundStyle(.secondary)
                            Text("定时调速结束后恢复自动").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }.padding(compact ? 0 : 14).frame(maxWidth: .infinity, alignment: .leading)
                    .background(compact ? Color.clear : Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }
    func mode(_ fan: Fan) -> String { ["auto": "由系统自动调节", "manual": "手动模式", "unknown": "模式未知"][fan.mode] ?? "模式未知" }
}

struct SensorsPanel: View {
    @ObservedObject var model: Monitor
    @State private var expanded = Set<String>()
    @State private var selected: Sensor?
    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Text("温度").font(.headline)
                Spacer()
                TextField("搜索传感器", text: $model.search).textFieldStyle(.roundedBorder).frame(width: 165).accessibilityLabel("搜索传感器")
            }
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(model.components.filter(matches)) { component in
                        let members = (model.snapshot?.sensors ?? []).filter { $0.group == component.group && (model.search.isEmpty || $0.title.localizedCaseInsensitiveContains(model.search) || component.title.localizedCaseInsensitiveContains(model.search)) }
                        Button {
                            if expanded.contains(component.id) { expanded.remove(component.id) } else { expanded.insert(component.id) }
                        } label: {
                            HStack(spacing: 9) {
                                Image(systemName: expanded.contains(component.id) ? "chevron.down" : "chevron.right").font(.system(size: 9, weight: .semibold)).frame(width: 9)
                                Image(systemName: component.symbol).foregroundStyle(.secondary).frame(width: 18)
                                Text(component.title)
                                Spacer(minLength: 6)
                                Text(temperature(component.value)).monospacedDigit()
                            }.padding(.horizontal, 10).frame(height: 32).contentShape(Rectangle())
                        }.buttonStyle(.plain).accessibilityLabel("\(component.title)，\(temperature(component.value))，\(expanded.contains(component.id) ? "收起" : "展开")")
                        Divider()
                        if expanded.contains(component.id) || !model.search.isEmpty {
                            ForEach(members.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }) { sensor in
                                Button { selected = sensor } label: {
                                    HStack {
                                        Text(sensor.title).lineLimit(1)
                                        Spacer()
                                        Text(temperature(sensor.usableValue)).monospacedDigit()
                                    }.font(.caption).padding(.leading, 40).padding(.trailing, 10).frame(height: 27).contentShape(Rectangle())
                                }.buttonStyle(.plain).foregroundStyle(sensor.usableValue == nil ? .secondary : .primary)
                            }
                        }
                    }
                    if !model.search.isEmpty && model.components.filter(matches).isEmpty { Text("没有匹配的部件或测点").foregroundStyle(.secondary).padding(20) }
                }
            }.background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
        }.sheet(item: $selected) { sensor in SensorInfo(model: model, sensor: sensor) }
    }
    func matches(_ c: Component) -> Bool {
        model.search.isEmpty || c.title.localizedCaseInsensitiveContains(model.search) ||
        (model.snapshot?.sensors ?? []).contains { $0.group == c.group && $0.title.localizedCaseInsensitiveContains(model.search) }
    }
}

struct SensorInfo: View {
    @ObservedObject var model: Monitor
    let sensor: Sensor
    var current: Sensor? { model.snapshot?.sensors.first { $0.id == sensor.id } }
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(sensor.title).font(.headline)
            Text(temperature(current?.usableValue)).font(.title2).monospacedDigit()
            LabeledContent("读数状态", value: current?.statusText ?? "连接中断")
            LabeledContent("原始标识", value: sensor.name)
            LabeledContent("来源", value: sensor.source.uppercased())
            Text(sensor.labelNote ?? "物理位置尚未确认。").font(.caption).foregroundStyle(.secondary)
            HStack { Spacer(); Button("完成") { dismiss() }.keyboardShortcut(.defaultAction) }
        }.padding(22).frame(width: 370)
    }
}

struct SpeedSheet: View {
    @ObservedObject var model: Monitor
    let fan: Fan
    @State private var rpm: Double
    @State private var seconds = 300
    @Environment(\.dismiss) private var dismiss
    init(model: Monitor, fan: Fan) {
        self.model = model; self.fan = fan
        _rpm = State(initialValue: min(max(fan.targetRpm ?? fan.actualRpm ?? 1000, fan.minRpm ?? 1000), fan.maxRpm ?? 4900))
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("调整\(fan.title)").font(.headline)
            HStack {
                Text("目标转速")
                Spacer()
                TextField("RPM", value: $rpm, format: .number.precision(.fractionLength(0))).textFieldStyle(.roundedBorder).frame(width: 88).accessibilityLabel("目标转速 RPM")
                Text("RPM").foregroundStyle(.secondary)
            }
            Slider(value: $rpm, in: (fan.minRpm ?? 1000)...(fan.maxRpm ?? 4900), step: 50).accessibilityLabel("目标转速")
            HStack { Text(rpmText(fan.minRpm)); Spacer(); Text(rpmText(fan.maxRpm)) }.font(.caption).foregroundStyle(.secondary)
            Picker("持续时间", selection: $seconds) {
                Text("15 秒（短时测试）").tag(15)
                Text("1 分钟").tag(60)
                Text("5 分钟").tag(300)
                Text("15 分钟").tag(900)
                Text("30 分钟").tag(1800)
                Text("60 分钟").tag(3600)
            }
            Text("到期后恢复系统自动。关闭主窗口后，本次调速仍会在菜单栏中继续。").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("开始定时调速") { model.set(fan, rpm: Int(rpm), duration: seconds); dismiss() }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(model.busy || !model.online || !rpm.isFinite || rpm < (fan.minRpm ?? 1000) || rpm > (fan.maxRpm ?? 4900))
            }
        }.padding(22).frame(width: 350)
    }
}

struct DetailsView: View {
    @ObservedObject var model: Monitor
    @State private var query = ""
    @State private var unknown = false
    @State private var selection: String?
    @Environment(\.dismiss) private var dismiss
    var rows: [Sensor] {
        (model.snapshot?.sensors ?? []).filter {
            (unknown || $0.group != "unknown") && (query.isEmpty || $0.title.localizedCaseInsensitiveContains(query) || $0.name.localizedCaseInsensitiveContains(query))
        }.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text("传感器详情").font(.headline); Spacer(); TextField("名称或原始编号", text: $query).textFieldStyle(.roundedBorder).frame(width: 220) }
            Toggle("显示未识别测点", isOn: $unknown).font(.caption)
            Table(rows, selection: $selection) {
                TableColumn("测点", value: \.title).width(min: 190)
                TableColumn("温度") { Text(temperature($0.usableValue)).monospacedDigit() }.width(80)
                TableColumn("状态", value: \.statusText).width(85)
                TableColumn("原始编号", value: \.name).width(125)
            }.frame(height: 275)
            if let item = rows.first(where: { $0.id == selection }) {
                Text("\(item.source.uppercased()) · 原始读数 \(temperature(item.rawValue))\n\(item.labelNote ?? "物理位置尚未确认。")")
                    .font(.caption).foregroundStyle(.secondary).frame(height: 48, alignment: .topLeading)
            } else { Text("选择测点查看命名依据与原始读数。概览为最高有效已识别测点，不是芯片平均温度。")
                    .font(.caption).foregroundStyle(.secondary).frame(height: 48, alignment: .topLeading) }
            HStack { Text("\(rows.count) 个测点").font(.caption).foregroundStyle(.secondary); Spacer(); Button("完成") { dismiss() }.keyboardShortcut(.defaultAction) }
        }.padding(20).frame(width: 620)
    }
}

struct ServiceSettings: View {
    @ObservedObject var model: Monitor
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("MacFan 设置").font(.headline)
            Label(model.helperStatus, systemImage: model.helperReady ? "checkmark.circle" : "exclamationmark.circle")
            Text("温度监测无需后台服务。首次调速或版本更新时，通过 macOS 系统认证安装受限 Helper。").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("检查状态") { model.refreshHelper() }.disabled(model.busy)
                Button("安装或更新服务") { model.installHelper() }.disabled(model.busy || model.demo || model.helperReady)
            }
            Divider()
            Text("关闭窗口后继续在菜单栏运行。退出应用时如有本应用发起的调速，会先确认恢复自动模式。").font(.caption).foregroundStyle(.secondary)
            HStack { Spacer(); Button("完成") { dismiss() }.keyboardShortcut(.defaultAction) }
        }.padding(22).frame(width: 390)
    }
}

struct SurfaceSheets: ViewModifier {
    @ObservedObject var model: Monitor
    func body(content: Content) -> some View {
        content.sheet(item: $model.selectedFan) { SpeedSheet(model: model, fan: $0) }
            .sheet(isPresented: $model.showDetails) { DetailsView(model: model) }
            .sheet(isPresented: $model.showSettings) { ServiceSettings(model: model) }
    }
}

struct MainView: View {
    @ObservedObject var model: Monitor
    var body: some View {
        VStack(spacing: 0) {
            SummaryStrip(model: model)
            Divider()
            HStack(alignment: .top, spacing: 0) {
                ScrollView { FanPanel(model: model).padding(16) }.frame(width: 285)
                Divider()
                SensorsPanel(model: model).padding(16)
            }
            if let error = model.error {
                HStack { Image(systemName: "exclamationmark.triangle"); Text(error).lineLimit(2); Spacer(); if !model.online { Button("重试连接") { model.retry() } } }
                    .font(.caption).foregroundStyle(.orange).padding(.horizontal, 16).padding(.vertical, 6)
            }
            Divider()
            HStack {
                Circle().fill(model.online ? (model.demo ? Color.orange : Color.green) : Color.gray).frame(width: 6, height: 6)
                Text(model.status).font(.caption)
                Spacer()
                if model.restoring { ProgressView().controlSize(.mini); Text("正在恢复自动…").font(.caption) }
                else if model.busy && !model.receipt.started { ProgressView().controlSize(.mini); Text(model.helperReady ? "正在准备调速…" : "等待系统认证…").font(.caption) }
                Button("传感器详情", systemImage: "info.circle") { model.showDetails = true }.buttonStyle(.plain).foregroundStyle(.blue)
            }.padding(.horizontal, 16).frame(height: 30)
        }.frame(width: 780, height: 340).background(Color(nsColor: .windowBackgroundColor))
            .toolbar {
                ToolbarItem(placement: .principal) { HStack { Text("MacFan").fontWeight(.semibold); Text(model.machine).foregroundStyle(.secondary) }.font(.system(size: 12)) }
                ToolbarItem { Button("设置", systemImage: "gearshape") { model.showSettings = true } }
            }.modifier(SurfaceSheets(model: model))
    }
}

struct MenuPanel: View {
    @ObservedObject var model: Monitor
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 3) { Text("MacFan").font(.headline); Text(model.machine).font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Button("设置", systemImage: "gearshape") { showMain(); model.showSettings = true }.labelStyle(.iconOnly).buttonStyle(.plain)
            }
            Divider()
            FanPanel(model: model, compact: true)
            if model.busy && model.receipt.started && !model.receipt.restored {
                TimelineView(.periodic(from: .now, by: 1)) { time in
                    let remaining = max(0, Int((model.receipt.deadline ?? time.date).timeIntervalSince(time.date)))
                    Text("目标 \(model.receipt.target ?? 0) RPM · 剩余 \(remaining / 60):\(String(format: "%02d", remaining % 60))").font(.caption).monospacedDigit().foregroundStyle(.secondary)
                }
            }
            Divider()
            HStack { Text("温度").font(.subheadline.weight(.semibold)); Spacer(); Button("更多…") { showMain(); model.showDetails = true }.buttonStyle(.plain).foregroundStyle(.secondary).font(.caption) }
            ForEach(model.components.filter { ["cpu", "gpu", "memory", "storage"].contains($0.group) }) { component in
                HStack(spacing: 8) {
                    Image(systemName: component.symbol).foregroundStyle(.secondary).frame(width: 18)
                    Text(component.title)
                    Spacer()
                    Text(temperature(component.value, precise: false)).monospacedDigit()
                }.font(.system(size: 12)).frame(height: 18)
            }
            if model.receipt.recoveryRequired && !model.receipt.restored {
                Button { model.restore(fanID: model.activeFanID) } label: { Text(model.restoring ? "正在恢复…" : "恢复系统自动").frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent).disabled(model.restoring)
            } else {
                Button { showMain(); model.selectedFan = model.fans.first } label: { Text(model.busy ? "正在准备…" : "调整转速…").frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent).disabled(model.busy || model.fans.first?.canControl != true || !model.online)
            }
            if let error = model.error { Text(error).font(.caption).foregroundStyle(.orange).lineLimit(2) }
            Divider()
            HStack {
                Button("打开主窗口", systemImage: "macwindow") { showMain() }.buttonStyle(.plain)
                Spacer()
                Text(model.status).font(.caption).foregroundStyle(.secondary)
                Button("退出", systemImage: "power") { NSApp.terminate(nil) }.labelStyle(.iconOnly).buttonStyle(.plain)
            }.font(.caption)
        }.padding(16).frame(width: 310).background(Color(nsColor: .windowBackgroundColor))
    }
    func showMain() { AppDelegate.instance?.showMain() }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    static weak var instance: AppDelegate?
    private var statusItem: NSStatusItem?
    private let popover = NSPopover()
    private var subscription: AnyCancellable?
    private var mainWindow: NSWindow?
    func applicationDidFinishLaunching(_ notification: Notification) {
        Self.instance = self
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem = item
        item.button?.image = NSImage(systemSymbolName: "fanblades", accessibilityDescription: "MacFan")
        item.button?.imagePosition = .imageLeading
        item.button?.font = .monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        item.button?.target = self
        item.button?.action = #selector(togglePopover(_:))
        item.button?.setAccessibilityLabel("MacFan 菜单栏")
        let host = NSHostingController(rootView: MenuPanel(model: Monitor.shared))
        host.sizingOptions = [.preferredContentSize]
        popover.contentViewController = host
        popover.behavior = .transient
        popover.animates = false
        subscription = Monitor.shared.$snapshot.sink { [weak self] sample in
            self?.statusItem?.button?.title = " \(rpmText(sample?.fans.first?.actualRpm)) RPM · \(temperature(sample?.temperatureOverview.first { $0.group == "cpu" }?.value, precise: false))"
        }
        DispatchQueue.main.async { [weak self] in
            self?.mainWindow = NSApp.windows.first { $0.identifier?.rawValue == "main" }
            self?.mainWindow?.isReleasedWhenClosed = false
            self?.mainWindow?.titleVisibility = .hidden
        }
        Monitor.shared.start()
    }
    @objc func togglePopover(_ sender: Any?) {
        guard let button = statusItem?.button else { return }
        if popover.isShown { popover.performClose(nil) }
        else { popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY) }
    }
    func showMain() {
        popover.performClose(nil)
        mainWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let model = Monitor.shared
        if model.restoring { return .terminateCancel }
        guard model.busy || model.receipt.recoveryRequired else { return .terminateNow }
        if model.busy && model.activeFanID == nil {
            let pending = NSAlert()
            pending.messageText = "后台服务操作尚未结束"
            pending.informativeText = "请完成或取消系统认证后再退出。"
            pending.runModal()
            return .terminateCancel
        }
        let alert = NSAlert()
        alert.messageText = "风扇调速尚未结束"
        alert.informativeText = "退出前需要确认恢复系统自动模式。"
        alert.addButton(withTitle: "恢复自动后退出")
        alert.addButton(withTitle: "取消")
        if alert.runModal() == .alertFirstButtonReturn {
            model.restore(fanID: model.activeFanID) { ok in NSApp.reply(toApplicationShouldTerminate: ok) }
            return .terminateLater
        }
        return .terminateCancel
    }
    func applicationWillTerminate(_ notification: Notification) { Monitor.shared.shutdown() }
}

@main
struct MacFanApplication: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = Monitor.shared
    init() {
        if CommandLine.arguments.contains("--smoke") && !CommandLine.arguments.contains("--demo") {
            fputs("Native smoke testing requires --demo.\n", stderr)
            exit(2)
        }
    }
    var body: some Scene {
        Window("MacFan", id: "main") { MainView(model: model) }
            .windowResizability(.contentSize)
            .windowToolbarStyle(.unifiedCompact)
            .windowStyle(.hiddenTitleBar)
            .commands { CommandGroup(replacing: .appSettings) { Button("设置…") { model.showSettings = true }.keyboardShortcut(",") } }
            .commands {
                CommandGroup(after: .windowArrangement) {
                    Button("显示主窗口") { AppDelegate.instance?.showMain() }.keyboardShortcut("0")
                    Button("显示菜单栏弹窗") { AppDelegate.instance?.togglePopover(nil) }.keyboardShortcut("m", modifiers: [.command, .shift])
                }
            }
    }
}
