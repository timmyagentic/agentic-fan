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
        let edited = try ruleDocument(document, enabled: true)
        let preserved = try JSONSerialization.jsonObject(with: Data(edited.utf8)) as! [String: Any]
        assert(ruleDocumentEnabled(edited) == true && preserved["note"] as? String == "preserve me")
        assert((preserved["rules"] as? [[String: Any]])?.first?["note"] as? String == "keep rule fields")
        assert(ruleDocumentEnabled(#"{"enabled":1}"#) == nil && ruleDocumentEnabled("invalid") == nil)
        let runtime = try decoder.decode(RulesRuntime.self, from: Data(#"{"enabled":true,"status":"active","config_path":"/tmp/rules.json","loaded_revision":"abc","desired_rpm":4900,"active_rule_ids":["cpu-hot"],"control_confirmed":false}"#.utf8))
        assert(runtime.mayOwnControl && runtime.statusText == "温度规则已触发" && !runtime.controlConfirmed && runtime.config == nil)
        let legacySnapshot = try decoder.decode(Snapshot.self, from: Data(#"{"sensors":[],"fans":[],"temperature_overview":[]}"#.utf8))
        assert(legacySnapshot.rules == nil, "Existing snapshot envelopes remain readable")
        let task = Command(["-c", "import time; print('{\"ok\":true,\"event\":\"ready\"}', flush=True); time.sleep(1.5)"])
        let started = Date()
        var latency: TimeInterval?
        try task.run(event: { _ in latency = latency ?? Date().timeIntervalSince(started) }, end: { code in
            assert(code == 0 && latency != nil && latency! < 1.0, "NDJSON events must arrive before the subprocess exits")
            print("PASS: incremental control events arrive before command completion")
            testInput()
        })
        print("PASS: native temperature validity, bounded input, editable multi-rule JSON and verified restoration")
        dispatchMain()
    }
    static func testInput() {
        let task = Command(["-c", "import sys,json; print(json.dumps({'ok':True,'data':json.load(sys.stdin)}),flush=True)"], input: #"{"enabled":false,"name":"Agent rule"}"#)
        var received = false
        do {
            try task.run(event: { message in received = (message["data"] as? [String: Any])?["name"] as? String == "Agent rule" }, end: { code in
                assert(code == 0 && received, "Rule JSON must reach CLI stdin and then EOF")
                testParentWatch()
            })
        } catch { fatalError(error.localizedDescription) }
    }
    static func testParentWatch() {
        let task = Command(["-c", "import sys; print('{\"ok\":true,\"event\":\"ready\"}',flush=True); sys.stdin.read(); print('{\"ok\":true,\"event\":\"finished\"}',flush=True)"], keepInputOpen: true)
        var finished = false
        do {
            try task.run(event: { message in
                if message["event"] as? String == "ready" {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                        assert(task.process.isRunning, "Parent-watch stdin must stay open while app runs")
                        task.closeInput()
                    }
                }
                if message["event"] as? String == "finished" { finished = true }
            }, end: { code in
                assert(code == 0 && finished, "Closing parent input must let runner finish orderly")
                print("PASS: rule JSON stdin and parent-watch EOF lifecycle")
                exit(0)
            })
        } catch { fatalError(error.localizedDescription) }
    }
}
