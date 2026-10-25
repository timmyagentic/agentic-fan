import Foundation

@main
struct ModelTests {
    static func main() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let fanData = Data(#"{"id":0,"name":"Fan 1","actual_rpm":1000,"target_rpm":1000,"min_rpm":1000,"max_rpm":4900,"mode":"auto","control_available":true}"#.utf8)
        let fan = try decoder.decode(Fan.self, from: fanData)
        let valid = try controlArguments(fan: fan, rpm: 1600, duration: 15)
        assert(valid == ["fans", "set", "--fan", "0", "--rpm", "1600", "--duration", "15"])
        for (rpm, duration) in [(0, 15), (999, 15), (4901, 15), (1600, 0), (1600, 3601)] {
            do { _ = try controlArguments(fan: fan, rpm: rpm, duration: duration); fatalError("Accepted invalid control") }
            catch { }
        }
        for status in ["invalid", "unverified", "unavailable"] {
            let bytes = Data("{\"id\":\"smc:TEST\",\"name\":\"TEST\",\"value\":92.0,\"raw_value\":92.0,\"status\":\"\(status)\",\"source\":\"smc\"}".utf8)
            let sensor = try decoder.decode(Sensor.self, from: bytes)
            assert(sensor.usableValue == nil, "Rejected raw reading leaked into UI")
            assert(temperature(sensor.usableValue) == "—")
        }
        assert(temperature(.nan) == "—")
        var receipt = ControlReceipt()
        receipt.receive(["event": "control_started", "data": ["target_rpm": 1600, "expires_at": "2026-10-18T13:54:10.123611+00:00"]])
        assert(receipt.started && receipt.target == 1600 && receipt.deadline != nil && receipt.recoveryRequired)
        assert(receipt.completionError(exitCode: 0) != nil, "Start cannot prove restoration")
        receipt.receive(["event": "control_confirmed", "data": [:]])
        assert(receipt.confirmed && receipt.completionError(exitCode: 0) != nil)
        receipt.receive(["event": "control_finished", "data": ["automatic_mode_verified": true]])
        assert(receipt.completionError(exitCode: 0) == nil && !receipt.recoveryRequired)
        assert(receipt.completionError(exitCode: 7) != nil, "Nonzero exit cannot pass")
        receipt.receive(["ok": false, "error": ["message": "restore failed"]])
        assert(receipt.completionError(exitCode: 0) == "restore failed")
        var beforeStarted = ControlReceipt()
        beforeStarted.receive(["error": ["code": "RESTORE_FAILED", "message": "restore failed"]])
        assert(beforeStarted.recoveryRequired)
        let document = #"{"version":1,"enabled":false,"fan":0,"note":"preserve me","rules":[{"id":"cpu-hot","enabled":true,"sensor_group":"cpu","trigger_celsius":99,"release_celsius":95,"release_seconds":5,"target_rpm":"max","note":"keep rule fields"},{"id":"gpu-warm","enabled":true,"sensor_group":"gpu","trigger_celsius":90,"release_celsius":85,"release_seconds":10,"target_rpm":4200}]}"#
        let config = try decoder.decode(RulesConfiguration.self, from: Data(document.utf8))
        assert(!config.enabled && config.rules.count == 2 && config.rules[0].targetRpm == .maximum && config.rules[1].targetRpm == .rpm(4200))
        assert(config.rules[0].summary.contains("99°C") && config.rules[0].summary.contains("95°C") && config.rules[0].summary.contains("5 秒"))
        let delayedDocument = document.replacingOccurrences(of: "\"trigger_celsius\":99", with: "\"trigger_celsius\":99,\"trigger_seconds\":10")
        let delayed = try decoder.decode(RulesConfiguration.self, from: Data(delayedDocument.utf8))
        assert(delayed.rules[0].summary.contains("持续 10 秒"))
        let hook = try decoder.decode(RuleHookRuntime.self, from: Data(#"{"running":false,"queued":0,"dropped":1,"last_result":{"status":"timed_out"}}"#.utf8))
        assert(hook.summary == "上次执行超时，已跳过 1 个事件")
        let exampleEvent = try JSONSerialization.jsonObject(with: Data(RuleHookExamples.event.utf8)) as! [String: Any]
        assert(exampleEvent["simulated"] as? Bool == true && exampleEvent["event"] as? String == "rule_triggered")
        let sampleBytes = try JSONSerialization.data(withJSONObject: RuleHookExamples.samples)
        print("HOOK_EXAMPLES_JSON:" + String(decoding: sampleBytes, as: UTF8.self))
        var hookDraft = try RuleHookDraft(document: delayedDocument)
        assert(!hookDraft.enabled)
        hookDraft.enabled = true
        hookDraft.program = "/Applications/A quoted app/notify"
        hookDraft.timeoutSeconds = 15
        let withHook = try hookDraft.applying(to: delayedDocument)
        let hookConfig = try decoder.decode(RulesConfiguration.self, from: Data(withHook.utf8))
        assert(hookConfig.hook?.program == hookDraft.program && hookConfig.hook?.timeoutSeconds == 15)
        assert(!hookConfig.enabled && hookConfig.rules.count == 2 && hookConfig.rules[0].triggerSeconds == 10,
               "Configuring a Hook must not enable or change temperature rules")
        let hookObject = try JSONSerialization.jsonObject(with: Data(withHook.utf8)) as! [String: Any]
        assert(hookObject["note"] as? String == "preserve me")
        assert((hookObject["rules"] as? [[String: Any]])?.first?["note"] as? String == "keep rule fields")
        let roundTrip = try RuleHookDraft(document: withHook)
        assert(roundTrip.enabled && roundTrip.program == hookDraft.program && roundTrip.timeoutSeconds == 15)
        for path in ["", "relative/script", "~/notify", "/tmp/a\0b"] {
            var invalid = hookDraft; invalid.program = path
            do { _ = try invalid.applying(to: document); fatalError("Accepted a non-executable-path command") }
            catch { }
        }
        for timeout in [0.0, 60.1, Double.nan, Double.infinity] {
            var invalid = hookDraft; invalid.timeoutSeconds = timeout
            do { _ = try invalid.applying(to: document); fatalError("Accepted unbounded Hook timeout") }
            catch { }
        }
        hookDraft.enabled = false
        let withoutHook = try hookDraft.applying(to: withHook)
        let disabledHook = try decoder.decode(RulesConfiguration.self, from: Data(withoutHook.utf8))
        assert(disabledHook.hook == nil && disabledHook.rules[0].triggerSeconds == 10)
        let edited = try ruleDocument(document, enabled: true)
        let preserved = try JSONSerialization.jsonObject(with: Data(edited.utf8)) as! [String: Any]
        assert(ruleDocumentEnabled(edited) == true && preserved["note"] as? String == "preserve me")
        assert((preserved["rules"] as? [[String: Any]])?.first?["note"] as? String == "keep rule fields")
        assert(ruleDocumentEnabled(#"{"enabled":1}"#) == nil && ruleDocumentEnabled("invalid") == nil)
        let runtime = try decoder.decode(RulesRuntime.self, from: Data(#"{"enabled":true,"status":"active","config_path":"/tmp/rules.json","loaded_revision":"abc","desired_rpm":4900,"active_rule_ids":["cpu-hot"],"control_confirmed":false}"#.utf8))
        assert(runtime.mayOwnControl && runtime.statusText == "温度规则已触发" && !runtime.controlConfirmed && runtime.config == nil)
        let legacySnapshot = try decoder.decode(Snapshot.self, from: Data(#"{"sensors":[],"fans":[],"temperature_overview":[]}"#.utf8))
        assert(legacySnapshot.rules == nil, "Existing snapshot envelopes remain readable")
        // Keep the producer alive until its first event is delivered. A reader
        // buffering until EOF cannot pass, even on a slow or cold CI runner.
        let task = Command(["-c", "import sys; print('{\"ok\":true,\"event\":\"ready\"}', flush=True); sys.stdin.read()"], keepInputOpen: true)
        var receivedWhileRunning = false
        try task.run(event: { message in
            guard message["event"] as? String == "ready" else { return }
            receivedWhileRunning = task.process.isRunning
            task.closeInput()
        }, end: { code in
            assert(code == 0 && receivedWhileRunning, "NDJSON must arrive while the parent-watch producer is still running")
            print("PASS: incremental events and parent-watch EOF arrive before command completion")
            fflush(stdout)
            testInput()
        })
        print("PASS: native temperature validity, bounded input, editable multi-rule JSON and verified restoration")
        fflush(stdout)
        // Process exit notifications are delivered on the launching run loop.
        // Match the Cocoa app: dispatchMain alone can leave waitUntilExit asleep
        // after an already-exited child, depending on exit/delivery ordering.
        RunLoop.main.run()
    }
    static func testInput() {
        let task = Command(["-c", "import sys,json; print(json.dumps({'ok':True,'data':json.load(sys.stdin)}),flush=True)"], input: #"{"enabled":false,"name":"Agent rule"}"#)
        var received = false
        do {
            try task.run(event: { message in received = (message["data"] as? [String: Any])?["name"] as? String == "Agent rule" }, end: { code in
                assert(code == 0 && received, "Rule JSON must reach CLI stdin and then EOF")
                print("PASS: rule JSON stdin and parent-watch EOF lifecycle")
                fflush(stdout)
                testFrameBoundaries()
            })
        } catch { fatalError(error.localizedDescription) }
    }
    static func testFrameBoundaries() {
        print("TRACE: starting oversized-frame probe"); fflush(stdout)
        let producer = #"""
import json, signal, sys, time
signal.signal(signal.SIGTERM, lambda *_: None)
sys.stdout.write('x' * 1048577)
sys.stdout.flush()
time.sleep(0.1)
print(json.dumps({'ok': True, 'event': 'control_finished', 'data': {'source': 'oversized-suffix'}}), flush=True)
print(json.dumps({'ok': True, 'event': 'control_finished', 'data': {'source': 'genuine'}}), flush=True)
print(json.dumps({'ok': False, 'event': 'sample', 'error': {'message': 'sample error must survive'}}), flush=True)
for sequence in range(1000):
    print(json.dumps({'ok': True, 'event': 'sample', 'data': {'sequence': sequence}}))
sys.stdout.flush()
"""#
        let task = Command(["-u", "-c", producer])
        var errors: [String] = []
        var receipts: [String] = []
        var lastSequence: Int?
        do {
            try task.run(event: { message in
                if let error = message["error"] as? [String: Any], let text = error["message"] as? String {
                    errors.append(text)
                    print("TRACE: frame error \(errors.count)"); fflush(stdout)
                }
                let data = message["data"] as? [String: Any]
                if message["event"] as? String == "control_finished", let source = data?["source"] as? String { receipts.append(source) }
                if let sequence = data?["sequence"] as? Int { lastSequence = sequence }
            }, end: { code in
                assert(code == 0, "Protocol failure must still drain genuine restoration receipts")
                assert(receipts == ["genuine"], "An oversized frame suffix must never become a receipt")
                assert(errors.count == 2 && errors.last == "sample error must survive", "Protocol errors are bounded and sample errors cannot be coalesced")
                assert(lastSequence == 999, "Latest sample must arrive before command completion")
                print("PASS: bounded frames, preserved errors, genuine receipts and final sample ordering")
                exit(0)
            })
            print("TRACE: frame producer pid \(task.process.processIdentifier)"); fflush(stdout)
        } catch { fatalError(error.localizedDescription) }
    }
}
