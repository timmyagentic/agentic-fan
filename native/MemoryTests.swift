import Foundation
import Darwin

// Runs the actual NDJSON transport without a GUI or any hardware backend. The
// producer stays alive at both checkpoints so worker autoreleases cannot hide
// behind thread/process teardown. See scripts/test-native-memory.py.
private struct MemoryProbeOptions {
    let producer: String
    let snapshot: String
    let continuation: String
    let label: String
    let warmup: Int
    let frames: Int
    let sleepMilliseconds: Double
    let limitBytes: UInt64
    let maximumFootprintBytes: UInt64
    let timeout: TimeInterval

    init() throws {
        let arguments = CommandLine.arguments
        func value(_ name: String, fallback: String? = nil) throws -> String {
            if let index = arguments.firstIndex(of: name), index + 1 < arguments.count { return arguments[index + 1] }
            if let fallback { return fallback }
            throw NSError(domain: "MemoryTests", code: 2, userInfo: [NSLocalizedDescriptionKey: "Missing \(name)"])
        }
        producer = try value("--producer-script")
        snapshot = try value("--snapshot")
        continuation = try value("--continue-path")
        label = try value("--label", fallback: "candidate")
        warmup = Int(try value("--warmup", fallback: "100")) ?? 0
        frames = Int(try value("--frames", fallback: "5000")) ?? 0
        sleepMilliseconds = Double(try value("--sleep-ms", fallback: "2")) ?? -1
        let limitMiB = Double(try value("--limit-mib", fallback: "64")) ?? 0
        let maximumMiB = Double(try value("--max-footprint-mib", fallback: "1280")) ?? 0
        timeout = Double(try value("--timeout", fallback: "120")) ?? 0
        guard warmup > 0, frames >= 4, sleepMilliseconds.isFinite, sleepMilliseconds >= 0,
              limitMiB.isFinite, limitMiB > 0, limitMiB < 65536,
              maximumMiB.isFinite, maximumMiB > limitMiB, maximumMiB < 65536,
              timeout.isFinite, timeout > 0 else {
            throw NSError(domain: "MemoryTests", code: 2, userInfo: [NSLocalizedDescriptionKey: "Invalid memory probe options"])
        }
        limitBytes = UInt64(limitMiB * 1_048_576)
        maximumFootprintBytes = UInt64(maximumMiB * 1_048_576)
    }
}

private func physicalFootprint() throws -> UInt64 {
    var information = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
    let result = withUnsafeMutablePointer(to: &information) { pointer in
        pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
        }
    }
    guard result == KERN_SUCCESS else {
        throw NSError(domain: "MemoryTests.task_info", code: Int(result), userInfo: [NSLocalizedDescriptionKey: "TASK_VM_INFO failed: \(result)"])
    }
    return information.phys_footprint
}

private final class MemoryProbe {
    let options: MemoryProbeOptions
    let started = Date()
    private let decoder: JSONDecoder = { let value = JSONDecoder(); value.keyDecodingStrategy = .convertFromSnakeCase; return value }()
    private var command: Command?
    private var latestSnapshot: Snapshot?
    private var pendingCheckpoint: (phase: String, deadline: Date)?
    private var warmupMeasurement: [String: Any]?
    private var floodMeasurement: [String: Any]?
    private var controls: [[String: Any]] = []
    private var failures: [String] = []
    private var controlReceipt = ControlReceipt()
    private var receivedSamples = 0
    private var lastSequence = 0
    private var maximumFrameLagMilliseconds = 0.0
    private var peakFootprint: UInt64 = 0
    private var barrierSequence: Int?
    private var producerFinished = false
    private var exitCode: Int32?
    private var memoryBudgetAborted = false

    init(_ options: MemoryProbeOptions) { self.options = options }

    func run() throws -> (report: [String: Any], passed: Bool) {
        let task = Command(["-u", options.producer, "--producer", "--snapshot", options.snapshot,
                            "--continue-path", options.continuation, "--warmup", String(options.warmup),
                            "--frames", String(options.frames)], keepInputOpen: true)
        command = task
        try task.run(event: { [weak self] message in self?.receive(message) }, end: { [weak self] code in
            guard let self else { return }
            if lastSequence != options.warmup + options.frames { fail("Final sample did not arrive before command completion") }
            if !producerFinished { fail("Producer completion event was lost or reordered") }
            exitCode = code
        })
        while exitCode == nil && Date().timeIntervalSince(started) < options.timeout {
            // This is a real main run loop, with an explicit outer pool boundary.
            // Measurements happen after callbacks and their temporary bridges drain.
            autoreleasepool {
                RunLoop.main.run(until: Date().addingTimeInterval(0.01))
            }
            try settleCheckpointIfReady()
            observeFootprint()
        }
        if exitCode == nil {
            fail("Memory replay timed out")
            task.stop()
        }
        if exitCode != 0 { fail("Producer exited with status \(exitCode.map(String.init) ?? "unavailable")") }
        if warmupMeasurement == nil || floodMeasurement == nil { fail("Missing live-process memory checkpoints") }
        let warmupBytes = warmupMeasurement?["physical_footprint_bytes"] as? UInt64 ?? 0
        let floodBytes = floodMeasurement?["physical_footprint_bytes"] as? UInt64 ?? 0
        let growth = Int64(floodBytes) - Int64(warmupBytes)
        if growth >= Int64(options.limitBytes) { fail("Physical footprint grew beyond the configured memory budget") }
        let peakGrowth = Int64(peakFootprint) - Int64(warmupBytes)
        if peakGrowth >= Int64(options.limitBytes) { fail("Peak physical footprint exceeded the configured memory budget") }
        let names = controls.compactMap { $0["event"] as? String }
        if names != ["control_started", "control_confirmed", "control_finished"] { fail("Non-sample control events were lost or reordered") }
        if !controlReceipt.started || !controlReceipt.confirmed || !controlReceipt.restored { fail("Control receipt progression was not retained") }
        let expected = options.warmup + options.frames
        if options.frames >= 100 && options.sleepMilliseconds >= 1 && receivedSamples >= expected {
            fail("Slow-consumer replay did not coalesce any samples")
        }
        if barrierSequence != expected { fail("Final sample did not arrive before the barrier") }
        if latestSnapshot?.sensors.count != 255 { fail("Replay must contain 255 sensor records") }
        let report: [String: Any] = [
            "label": options.label, "simulated": true, "hardware_writes": false,
            "metric": "task_info(TASK_VM_INFO).phys_footprint", "pid": getpid(),
            "elapsed_seconds": Date().timeIntervalSince(started), "warmup_frames": options.warmup,
            "replay_frames": options.frames, "sensor_count": latestSnapshot?.sensors.count ?? 0,
            "slow_consumer_sleep_ms": options.sleepMilliseconds, "growth_limit_bytes": options.limitBytes,
            "maximum_footprint_bytes": options.maximumFootprintBytes, "aborted_on_memory_budget": memoryBudgetAborted,
            "warmup": warmupMeasurement ?? [:], "after_replay": floodMeasurement ?? [:],
            "growth_bytes": growth, "peak_physical_footprint_bytes": peakFootprint,
            "peak_growth_bytes": peakGrowth,
            "received_sample_callbacks": receivedSamples, "produced_sample_frames": expected,
            "coalesced_sample_frames": max(0, expected - receivedSamples),
            "last_sample_sequence": lastSequence, "barrier_sample_sequence": barrierSequence ?? -1,
            "maximum_sample_lag_ms": maximumFrameLagMilliseconds, "control_events": controls,
            "producer_finished_event": producerFinished, "exit_code": exitCode.map(Int.init) ?? -1,
            "passed": failures.isEmpty, "failures": failures
        ]
        return (report, failures.isEmpty)
    }

    private func receive(_ message: [String: Any]) {
        guard message["simulated"] as? Bool == true else { fail("Replay event was not explicitly simulated"); return }
        let event = message["event"] as? String ?? ""
        let data = message["data"] as? [String: Any] ?? [:]
        switch event {
        case "sample":
            guard let sequence = message["test_sequence"] as? Int else { fail("Sample has no test sequence"); return }
            if sequence <= lastSequence { fail("Sample sequence regressed") }
            lastSequence = sequence
            receivedSamples += 1
            if let emitted = message["emitted_uptime_ns"] as? UInt64 {
                let now = DispatchTime.now().uptimeNanoseconds
                maximumFrameLagMilliseconds = max(maximumFrameLagMilliseconds, now >= emitted ? Double(now - emitted) / 1_000_000 : 0)
            }
            autoreleasepool {
                do {
                    let encoded = try JSONSerialization.data(withJSONObject: data)
                    latestSnapshot = try decoder.decode(Snapshot.self, from: encoded)
                } catch { fail("Snapshot decode failed: \(error.localizedDescription)") }
            }
            if options.sleepMilliseconds > 0 { Thread.sleep(forTimeInterval: options.sleepMilliseconds / 1000) }
            // Sampling here also observes a pending-main-queue high-water mark.
            if receivedSamples % 25 == 0 { observeFootprint() }
        case "control_started", "control_confirmed", "control_finished":
            let after = data["after_sequence"] as? Int ?? -1
            if lastSequence > after { fail("A future sample overtook a control event") }
            controls.append(["event": event, "last_sample_sequence": lastSequence, "after_sequence": after])
            controlReceipt.receive(message)
        case "memory_barrier":
            barrierSequence = lastSequence
            if lastSequence != data["expected_sequence"] as? Int { fail("Latest sample was not flushed before the barrier") }
        case "memory_checkpoint":
            guard let phase = data["phase"] as? String else { fail("Checkpoint has no phase"); return }
            if lastSequence != data["expected_sequence"] as? Int { fail("Latest sample was not flushed before checkpoint \(phase)") }
            if pendingCheckpoint != nil { fail("Checkpoints overlapped") }
            pendingCheckpoint = (phase, Date().addingTimeInterval(0.15))
        case "memory_producer_finished": producerFinished = true
        default:
            fail("Unexpected replay event: \(event)")
        }
    }

    private func settleCheckpointIfReady() throws {
        guard let checkpoint = pendingCheckpoint, Date() >= checkpoint.deadline else { return }
        pendingCheckpoint = nil
        guard let command, command.process.isRunning else { fail("Producer ended before memory measurement"); return }
        let bytes = try physicalFootprint()
        peakFootprint = max(peakFootprint, bytes)
        let measurement: [String: Any] = ["physical_footprint_bytes": bytes,
                                          "received_sample_callbacks": receivedSamples,
                                          "last_sample_sequence": lastSequence,
                                          "producer_running": true]
        switch checkpoint.phase {
        case "warmup":
            warmupMeasurement = measurement
            try Data("continue\n".utf8).write(to: URL(fileURLWithPath: options.continuation), options: .atomic)
        case "after_replay":
            floodMeasurement = measurement
            command.closeInput()
        default: fail("Unknown checkpoint phase: \(checkpoint.phase)")
        }
    }

    private func observeFootprint() {
        guard let current = try? physicalFootprint() else { return }
        peakFootprint = max(peakFootprint, current)
        if current >= options.maximumFootprintBytes && !memoryBudgetAborted {
            memoryBudgetAborted = true
            fail("Replay stopped at the emergency physical-footprint budget")
            floodMeasurement = ["physical_footprint_bytes": current,
                                "received_sample_callbacks": receivedSamples,
                                "last_sample_sequence": lastSequence,
                                "producer_running": command?.process.isRunning == true,
                                "early_memory_budget_abort": true]
            command?.stop()
        }
    }
    private func fail(_ message: String) {
        if !failures.contains(message) { failures.append(message) }
    }
}

@main
struct MemoryTests {
    static func main() {
        do {
            let probe = MemoryProbe(try MemoryProbeOptions())
            let result = try withExtendedLifetime(probe) { try probe.run() }
            let bytes = try JSONSerialization.data(withJSONObject: result.report, options: [.sortedKeys])
            print(String(decoding: bytes, as: UTF8.self))
            fflush(stdout)
            exit(result.passed ? 0 : 1)
        } catch {
            let failure: [String: Any] = ["passed": false, "simulated": true, "error": error.localizedDescription]
            let bytes = try! JSONSerialization.data(withJSONObject: failure, options: [.sortedKeys])
            print(String(decoding: bytes, as: UTF8.self))
            exit(2)
        }
    }
}
