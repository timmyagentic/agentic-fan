import Foundation
import Darwin

// One NDJSON stream feeds both native surfaces. The helper remains the only writer.
final class Command {
    let process = Process()
    private let output = Pipe()
    init(_ arguments: [String]) {
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
        process.standardInput = FileHandle.nullDevice
    }
    func run(event: @escaping ([String: Any]) -> Void, end: @escaping (Int32) -> Void) throws {
        try process.run()
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
            let code = process.terminationStatus
            DispatchQueue.main.async { end(code) }
        }
    }
    func stop() { if process.isRunning { process.terminate() } }
}

