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
        let edited = try ruleDocument(document, enabled: true)
        let preserved = try JSONSerialization.jsonObject(with: Data(edited.utf8)) as! [String: Any]
        assert(ruleDocumentEnabled(edited) == true && preserved["note"] as? String == "preserve me")
        assert((preserved["rules"] as? [[String: Any]])?.first?["note"] as? String == "keep rule fields")
        assert(ruleDocumentEnabled(#"{"enabled":1}"#) == nil && ruleDocumentEnabled("invalid") == nil)
        var curveDraft = try CurveDraft(document: document, fans: [fan])
        curveDraft.mode = .curve
        curveDraft.nodes = [CurveNode(60, 1200), CurveNode(80, 2200), CurveNode(90, 3200), CurveNode(100, 4500)]
        let curveJSON = try curveDraft.document(fans: [fan])
        let curveObject = try JSONSerialization.jsonObject(with: Data(curveJSON.utf8)) as! [String: Any]
        assert(curveObject["note"] as? String == "preserve me", "Curve edits must preserve unrelated settings")
        assert((curveObject["rules"] as? [[String: Any]])?.count == 2)
        let curveConfig = try decoder.decode(RulesConfiguration.self, from: Data(curveJSON.utf8))
        assert(curveConfig.enabled && curveConfig.mode == "curve" && curveConfig.curve?.nodes.count == 4)
        assert(curveDraft.addNode() != nil && curveDraft.nodes.count == 5)
        let added = curveDraft.nodes.first { $0.temperatureCelsius == 70 }!
        assert(added.targetRpm == 1700)
        curveDraft.removeNode(added.id)
        assert(curveDraft.nodes.count == 4)
        curveDraft.nodes[1].temperatureCelsius = 60
        do { _ = try curveDraft.document(fans: [fan]); fatalError("Duplicate curve temperatures accepted") } catch { }
        curveDraft.mode = .automatic
        let automaticJSON = try curveDraft.document(fans: [fan])
        assert(ruleDocumentEnabled(automaticJSON) == false, "Automatic mode must remain available even with an invalid draft")
        let runtime = try decoder.decode(RulesRuntime.self, from: Data(#"{"enabled":true,"status":"active","config_path":"/tmp/rules.json","loaded_revision":"abc","desired_rpm":4900,"active_rule_ids":["cpu-hot"],"control_confirmed":false}"#.utf8))
        assert(runtime.mayOwnControl && runtime.statusText == "温度规则已触发" && !runtime.controlConfirmed && runtime.config == nil)
        let feedbackDate = Date(timeIntervalSince1970: 1793448001)
        let feedbackJSON = #"{"status":"tracking","target_rpm":1316,"actual_rpm":1317,"sampled_at":"2026-10-31T12:00:00+00:00","pending_update":true}"#
        let feedback = try decoder.decode(ControlFeedback.self, from: Data(feedbackJSON.utf8))
        assert(feedback.summary(at: feedbackDate).contains("跟随正常"))
        assert(feedback.summary(at: feedbackDate).contains("目标更新中"))
        assert(feedback.summary(at: feedbackDate.addingTimeInterval(3)) == "转速反馈已过期")
        assert(feedback.summary(at: feedbackDate.addingTimeInterval(-2)) == "转速反馈已过期")
        let drifting = try decoder.decode(ControlFeedback.self, from: Data(feedbackJSON.replacingOccurrences(of: "tracking", with: "adjusting").utf8))
        assert(drifting.summary(at: feedbackDate).contains("正在调速"))
        assert(!drifting.summary(at: feedbackDate).contains("跟随正常"))
        let stoppedFeedback = try decoder.decode(ControlFeedback.self, from: Data(feedbackJSON.replacingOccurrences(of: "tracking", with: "stopped").utf8))
        assert(stoppedFeedback.summary(at: feedbackDate) == "跟随监测已结束")
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
