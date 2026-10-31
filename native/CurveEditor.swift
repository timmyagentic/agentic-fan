import SwiftUI
import AppKit

struct CurveSheet: View {
    @ObservedObject var model: Monitor
    @Environment(\.dismiss) private var dismiss
    @State private var draft = CurveDraft()
    @State private var selected: UUID?
    @State private var loadError: String?
    private var locked: Bool { model.rulesLoading || model.rulesSaving || model.busy || model.rulesStopping }
    private var fan: Fan? { model.fans.first { $0.id == draft.fan } }
    private var issue: String? {
        guard !draft.source.isEmpty else { return loadError }
        do { _ = try draft.document(fans: model.fans); return nil }
        catch { return error.localizedDescription }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("温控曲线").font(.title2.weight(.semibold))
                    Text("设置温度与转速节点，让风扇平滑跟随。").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                if model.demo { Text("演示数据").font(.caption).foregroundStyle(.orange) }
            }
            Picker("控制方式", selection: $draft.mode) {
                ForEach(CurveMode.allCases, id: \.self) { Text($0.title).tag($0) }
            }.pickerStyle(.segmented).disabled(locked || draft.source.isEmpty)
            if draft.mode == .curve {
                HStack(spacing: 18) {
                    Picker("温度来源", selection: $draft.sensorGroup) {
                        ForEach([("cpu", "CPU"), ("gpu", "GPU"), ("memory", "内存"), ("storage", "SSD"),
                                 ("wireless", "无线网络"), ("power", "电源管理"), ("battery", "电池"),
                                 ("neural", "神经网络"), ("system", "系统")], id: \.0) { Text($0.1).tag($0.0) }
                    }.frame(width: 210)
                    if model.fans.count > 1 {
                        Picker("风扇", selection: $draft.fan) {
                            ForEach(model.fans) { Text($0.title + " \($0.id + 1)").tag($0.id) }
                        }
                    }
                    Spacer(minLength: 0)
                    Text("\(rpmText(fan?.minRpm))–\(rpmText(fan?.maxRpm)) RPM").font(.caption).foregroundStyle(.secondary)
                }
                CurvePlot(nodes: $draft.nodes, selected: $selected, minimum: fan?.minRpm ?? 1000, maximum: fan?.maxRpm ?? 4900)
                    .disabled(locked)
                HStack {
                    Text("节点").font(.headline)
                    Text("拖动图上的节点，或直接编辑数值").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button { selected = draft.addNode() } label: { Label("添加节点", systemImage: "plus") }
                        .disabled(locked || draft.nodes.count >= 16)
                }
                HStack {
                    Text("温度（°C）").frame(width: 155, alignment: .leading)
                    Text("转速（RPM）")
                    Spacer()
                }.font(.caption).foregroundStyle(.secondary)
                ScrollView {
                    VStack(spacing: 7) {
                        ForEach($draft.nodes) { $node in
                            HStack(spacing: 12) {
                                Circle().fill(selected == node.id ? Color.accentColor : Color.secondary.opacity(0.35)).frame(width: 7, height: 7)
                                TextField("温度", value: $node.temperatureCelsius, format: .number.precision(.fractionLength(0...1)))
                                    .frame(width: 118).accessibilityLabel("节点温度 \(ruleNumber(node.temperatureCelsius))")
                                TextField("转速", value: $node.targetRpm, format: .number.grouping(.never))
                                    .frame(width: 155).accessibilityLabel("\(ruleNumber(node.temperatureCelsius)) 度节点转速")
                                Text("RPM").font(.caption).foregroundStyle(.secondary)
                                Spacer()
                                Button { draft.removeNode(node.id) } label: { Image(systemName: "minus.circle") }
                                    .buttonStyle(.plain).foregroundStyle(.secondary).disabled(draft.nodes.count <= 2)
                                    .accessibilityLabel("删除 \(ruleNumber(node.temperatureCelsius)) 度节点")
                            }.textFieldStyle(.roundedBorder).padding(.horizontal, 2)
                        }
                    }.padding(.vertical, 2)
                }.frame(maxHeight: 148).disabled(locked)
                HStack {
                    Text("平滑速度").font(.callout)
                    TextField("每秒 RPM", value: $draft.ramp, format: .number.grouping(.never)).textFieldStyle(.roundedBorder)
                        .frame(width: 75).accessibilityLabel("每秒最大转速变化")
                    Text("RPM / 秒").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Text("相邻节点线性插值").font(.caption).foregroundStyle(.secondary)
                }.disabled(locked)
                Text("低于首节点或高于末节点时，使用对应端点转速。保存后才生效。")
                    .font(.caption).foregroundStyle(.secondary)
            } else if draft.mode == .automatic {
                VStack(spacing: 12) {
                    Image(systemName: "fanblades").font(.system(size: 40)).foregroundStyle(.secondary)
                    Text("由系统自动调节转速").font(.headline)
                    Text("应用后结束本应用的曲线或规则控制，已保存的节点会保留。")
                        .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    Text(model.rulesSummary).font(.callout).fixedSize(horizontal: false, vertical: true)
                    Text("保留原有阈值与触发代码配置。选择自定义曲线可改用连续调速。")
                        .font(.callout).foregroundStyle(.secondary)
                    Button("编辑温度规则 JSON…") {
                        dismiss()
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { model.showRules = true }
                    }.disabled(locked)
                }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading).padding(.top, 10)
            }
            if draft.mode == .curve { Spacer(minLength: 0) }
            if model.rulesChangedExternally {
                Text("配置已由其他窗口或 Agent 修改，请重新读取后再保存。").font(.caption).foregroundStyle(.orange)
            }
            if let text = issue ?? model.rulesEditorError ?? loadError {
                Text(text).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            } else if let notice = model.rulesNotice {
                Text(notice).font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button("重新读取") { model.reloadRules() }.disabled(locked)
                if locked { ProgressView().controlSize(.small) }
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction).disabled(model.rulesSaving)
                Button(model.rulesSaving ? "正在应用…" : "保存并应用") {
                    NSApp.keyWindow?.makeFirstResponder(nil)
                    DispatchQueue.main.async {
                        do { model.rulesDraft = try draft.document(fans: model.fans); model.saveRules() }
                        catch { loadError = error.localizedDescription }
                    }
                }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
                    .disabled(locked || draft.source.isEmpty || issue != nil || model.rulesChangedExternally || model.restoring || !model.online)
            }
        }.padding(22).frame(width: 660, height: 690)
            .onAppear { loadDocument(model.rulesDraft); model.reloadRules() }
            .onChange(of: model.rulesDraft) { _, document in
                loadDocument(document)
            }
    }
    private func loadDocument(_ document: String) {
        guard !document.isEmpty else { return }
        do { draft = try CurveDraft(document: document, fans: model.fans); selected = nil; loadError = nil }
        catch { loadError = error.localizedDescription }
    }
}

private struct CurvePlot: View {
    @Binding var nodes: [CurveNode]
    @Binding var selected: UUID?
    let minimum: Double
    let maximum: Double
    @State private var dragDomain: ClosedRange<Double>?
    private var domain: ClosedRange<Double> {
        let valid = nodes.map(\.temperatureCelsius).filter(\.isFinite)
        return dragDomain ?? (min(30, (valid.min() ?? 60) - 10)...max(110, (valid.max() ?? 100) + 10))
    }
    var body: some View {
        GeometryReader { geometry in
            let area = CGRect(x: 48, y: 16, width: geometry.size.width - 66, height: geometry.size.height - 48)
            let range = domain
            let span = max(1, maximum - minimum)
            let point: (CurveNode) -> CGPoint = { node in
                CGPoint(x: area.minX + CGFloat((node.temperatureCelsius - range.lowerBound) / (range.upperBound - range.lowerBound)) * area.width,
                        y: area.maxY - CGFloat((Double(node.targetRpm) - minimum) / span) * area.height)
            }
            ZStack(alignment: .topLeading) {
                Path { path in
                    for step in 0...4 {
                        let y = area.minY + CGFloat(step) / 4 * area.height
                        path.move(to: CGPoint(x: area.minX, y: y)); path.addLine(to: CGPoint(x: area.maxX, y: y))
                    }
                }.stroke(Color.secondary.opacity(0.13), lineWidth: 1)
                ForEach(0...2, id: \.self) { step in
                    Text(String(Int(maximum - Double(step) / 2 * span))).font(.system(size: 10)).foregroundStyle(.secondary)
                        .position(x: 23, y: area.minY + CGFloat(step) / 2 * area.height)
                }
                ForEach(0...4, id: \.self) { step in
                    Text("\(Int(range.lowerBound + Double(step) / 4 * (range.upperBound - range.lowerBound)))°")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                        .position(x: area.minX + CGFloat(step) / 4 * area.width, y: area.maxY + 17)
                }
                Path { path in
                    let sorted = nodes.filter { $0.temperatureCelsius.isFinite }.sorted { $0.temperatureCelsius < $1.temperatureCelsius }
                    if let first = sorted.first {
                        path.move(to: CGPoint(x: area.minX, y: point(first).y))
                        for node in sorted { path.addLine(to: point(node)) }
                        if let last = sorted.last { path.addLine(to: CGPoint(x: area.maxX, y: point(last).y)) }
                    }
                }.stroke(Color.accentColor, style: StrokeStyle(lineWidth: 2, lineJoin: .round))
                    .clipShape(Rectangle().path(in: area))
                ForEach(nodes.filter { $0.temperatureCelsius.isFinite }) { node in
                    Circle().fill(Color.accentColor).frame(width: selected == node.id ? 12 : 10, height: selected == node.id ? 12 : 10)
                        .overlay(Circle().stroke(Color(nsColor: .windowBackgroundColor), lineWidth: 2))
                        .frame(width: 26, height: 26).contentShape(Circle()).position(point(node))
                        .accessibilityLabel("曲线节点 \(ruleNumber(node.temperatureCelsius)) 度，\(node.targetRpm) RPM")
                        .gesture(DragGesture(minimumDistance: 0, coordinateSpace: .named("curve-plot"))
                            .onChanged { value in
                                if dragDomain == nil { dragDomain = range }
                                selected = node.id
                                guard let index = nodes.firstIndex(where: { $0.id == node.id }) else { return }
                                let sorted = nodes.sorted { $0.temperatureCelsius < $1.temperatureCelsius }
                                let order = sorted.firstIndex(where: { $0.id == node.id })!
                                let lowTemperature = order > 0 ? sorted[order - 1].temperatureCelsius + 0.1 : -40
                                let highTemperature = order + 1 < sorted.count ? sorted[order + 1].temperatureCelsius - 0.1 : 150
                                let temperature = range.lowerBound + Double((value.location.x - area.minX) / area.width) * (range.upperBound - range.lowerBound)
                                let lowRpm = max(minimum, order > 0 ? Double(sorted[order - 1].targetRpm) : minimum)
                                let highRpm = min(maximum, order + 1 < sorted.count ? Double(sorted[order + 1].targetRpm) : maximum)
                                let rpm = minimum + Double((area.maxY - value.location.y) / area.height) * span
                                nodes[index].temperatureCelsius = (min(highTemperature, max(lowTemperature, temperature)) * 10).rounded() / 10
                                nodes[index].targetRpm = Int(min(highRpm, max(lowRpm, (rpm / 10).rounded() * 10)))
                            }.onEnded { _ in dragDomain = nil })
                }
            }.coordinateSpace(name: "curve-plot")
        }.frame(height: 185).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
    }
}
