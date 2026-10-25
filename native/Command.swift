import Foundation
import Darwin

// A slow UI needs the newest monitoring sample, not an ever-growing history.
// Seal each batch before a non-sample event so control/finish receipts stay FIFO.
private final class CommandDelivery {
    private final class Batch {
        var message: [String: Any]?
        init(_ message: [String: Any]) { self.message = message }
    }
    private let lock = NSLock()
    private var pending: Batch?

    func send(_ message: [String: Any], event: @escaping ([String: Any]) -> Void) {
        lock.lock()
        if message["event"] as? String == "sample", message["ok"] as? Bool == true {
            if let pending {
                pending.message = message
                lock.unlock()
                return
            }
            let batch = Batch(message)
            pending = batch
            lock.unlock()
            DispatchQueue.main.async { [self] in
                lock.lock()
                let latest = batch.message
                batch.message = nil
                if pending === batch { pending = nil }
                lock.unlock()
                if let latest { event(latest) }
            }
        } else {
            pending = nil
            lock.unlock()
            DispatchQueue.main.async { event(message) }
        }
    }
}

// One NDJSON stream feeds both native surfaces. The helper remains the only writer.
final class Command {
    let process = Process()
    private let output = Pipe()
    private let input: String?
    private let keepInputOpen: Bool
    private let maximumFrameBytes: Int
    private var inputPipe: Pipe?
    private let inputLock = NSLock()
    private let delivery = CommandDelivery()
    init(_ arguments: [String], input: String? = nil, keepInputOpen: Bool = false, maximumFrameBytes: Int = 1_048_576) {
        self.input = input
        self.keepInputOpen = keepInputOpen
        self.maximumFrameBytes = maximumFrameBytes
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--backend"), i + 1 < args.count {
            process.executableURL = URL(fileURLWithPath: args[i + 1])
        } else {
            process.executableURL = URL(fileURLWithPath: args[0]).deletingLastPathComponent().appendingPathComponent("agenticfan")
        }
        process.arguments = (args.contains("--module") ? ["-m", "agenticfan"] : []) + arguments
        process.environment = ProcessInfo.processInfo.environment.merging(["PYINSTALLER_RESET_ENVIRONMENT": "1"]) { _, new in new }
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        if input != nil || keepInputOpen {
            let pipe = Pipe()
            inputPipe = pipe
            process.standardInput = pipe
        } else { process.standardInput = FileHandle.nullDevice }
    }
    func run(event: @escaping ([String: Any]) -> Void, end: @escaping (Int32) -> Void) throws {
        try process.run()
        inputPipe?.fileHandleForReading.closeFile()
        if let input {
            DispatchQueue.global(qos: .utility).async { [self] in
                inputLock.lock()
                var failure: Error?
                do { try inputPipe?.fileHandleForWriting.write(contentsOf: Data(input.utf8)) }
                catch { failure = error }
                inputLock.unlock()
                if let failure {
                    DispatchQueue.main.async { event(["ok": false, "error": ["message": "无法提交配置：\(failure.localizedDescription)"]]) }
                    if process.isRunning { process.terminate() }
                }
                if !keepInputOpen { closeInput() }
            }
        } else if !keepInputOpen { closeInput() }
        DispatchQueue.global(qos: .utility).async { [self] in
            var buffer = Data()
            var chunk = [UInt8](repeating: 0, count: 65536)
            let maximumFrameBytes = self.maximumFrameBytes
            var protocolErrorReported = false
            var discardingOversizedFrame = false
            func rejectFrame() {
                if !protocolErrorReported {
                    protocolErrorReported = true
                    delivery.send(["ok": false, "error": ["message": "无法读取后台返回的数据。"]], event: event)
                    if process.isRunning { process.terminate() }
                }
            }
            while true {
                // This GCD work item lives for the whole monitoring session.
                // Drain Foundation's autoreleased JSON objects after each read,
                // rather than retaining hours of sensor dictionaries until EOF.
                let keepReading = autoreleasepool { () -> Bool in
                    // A POSIX read delivers short control events immediately.
                    let count = chunk.withUnsafeMutableBytes {
                        Darwin.read(output.fileHandleForReading.fileDescriptor, $0.baseAddress, $0.count)
                    }
                    if count < 0 && errno == EINTR { return true }
                    if count <= 0 { return false }
                    var bytes = chunk.prefix(count)
                    if discardingOversizedFrame {
                        // A read can end in the middle of a rejected frame. Its
                        // suffix must never be accepted as a separate receipt.
                        guard let newline = bytes.firstIndex(of: 10) else { return true }
                        bytes = bytes.suffix(from: newline + 1)
                        discardingOversizedFrame = false
                    }
                    buffer.append(contentsOf: bytes)
                    while let newline = buffer.firstIndex(of: 10) {
                        // Check before decoding, including complete oversized lines.
                        let size = buffer.distance(from: buffer.startIndex, to: newline)
                        if size > maximumFrameBytes {
                            buffer.removeSubrange(...newline)
                            rejectFrame()
                            continue
                        }
                        let line = Data(buffer[..<newline])
                        buffer.removeSubrange(...newline)
                        if let message = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                            delivery.send(message, event: event)
                        } else if !line.isEmpty { rejectFrame() }
                    }
                    if buffer.count > maximumFrameBytes {
                        buffer.removeAll(keepingCapacity: false)
                        discardingOversizedFrame = true
                        rejectFrame()
                    }
                    return true
                }
                if !keepReading { break }
            }
            process.waitUntilExit()
            closeInput()
            let code = process.terminationStatus
            DispatchQueue.main.async { end(code) }
        }
    }
    // A rules runner watches stdin EOF so the child restores its owned fan control
    // even if the native parent disappears. Normal application quit waits for it.
    func closeInput() {
        inputLock.lock()
        defer { inputLock.unlock() }
        try? inputPipe?.fileHandleForWriting.close()
        inputPipe = nil
    }
    func stop() { closeInput(); if process.isRunning { process.terminate() } }
}

