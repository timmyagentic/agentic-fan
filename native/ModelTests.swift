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
        let task = Command(["-c", "import time; print('{\"ok\":true,\"event\":\"ready\"}', flush=True); time.sleep(1.5)"])
        let started = Date()
        var latency: TimeInterval?
        try task.run(event: { _ in latency = latency ?? Date().timeIntervalSince(started) }, end: { code in
            assert(code == 0 && latency != nil && latency! < 1.0, "NDJSON events must arrive before the subprocess exits")
            print("PASS: incremental control events arrive before command completion")
            exit(0)
        })
        print("PASS: native temperature validity, bounded input, event progression and verified restoration")
        dispatchMain()
    }
}
