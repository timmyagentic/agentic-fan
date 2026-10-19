import Foundation
import Darwin

// One NDJSON stream feeds both native surfaces. The helper remains the only writer.
final class Command {
    let process = Process()
    private let output = Pipe()
    private let input: String?
    private let keepInputOpen: Bool
    private var inputPipe: Pipe?
    private let inputLock = NSLock()
    init(_ arguments: [String], input: String? = nil, keepInputOpen: Bool = false) {
        self.input = input
        self.keepInputOpen = keepInputOpen
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--backend"), i + 1 < args.count {
            process.executableURL = URL(fileURLWithPath: args[i + 1])
        } else {
            process.executableURL = URL(fileURLWithPath: args[0]).deletingLastPathComponent().appendingPathComponent("macfan")
        }
        process.arguments = (args.contains("--module") ? ["-m", "macfan"] : []) + arguments
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
            while true {
                // Foundation may fill the requested buffer before returning. A single
                // POSIX read delivers short control events immediately, before EOF.
                let count = chunk.withUnsafeMutableBytes {
                    Darwin.read(output.fileHandleForReading.fileDescriptor, $0.baseAddress, $0.count)
                }
                if count < 0 && errno == EINTR { continue }
                if count <= 0 { break }
                buffer.append(contentsOf: chunk.prefix(count))
                while let newline = buffer.firstIndex(of: 10) {
                    let line = Data(buffer[..<newline])
                    buffer.removeSubrange(...newline)
                    if let message = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                        DispatchQueue.main.async { event(message) }
                    } else if !line.isEmpty {
                        DispatchQueue.main.async { event(["ok": false, "error": ["message": "无法读取后台返回的数据。"]]) }
                        process.terminate()
                    }
                }
                if buffer.count > 1_048_576 { process.terminate(); break }
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

