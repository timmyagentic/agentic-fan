import Foundation
import Darwin

struct HookScript {
    static let maximumBytes = 256 * 1024
    static let emptyTemplate = """
    #!/bin/sh
    # 在这里编写温度规则触发后要执行的代码。
    # 事件 JSON 通过标准输入传入；默认不执行任何动作。


    """

    let url: URL
    private(set) var contents: String
    private var original: Data?
    var exists: Bool { original != nil }

    static func defaultURL(rulesPath: String) -> URL {
        URL(fileURLWithPath: rulesPath).deletingLastPathComponent()
            .appendingPathComponent("hooks/threshold-hook.sh")
    }

    init(url: URL, allowNew: Bool) throws {
        self.url = url
        original = try Self.read(url)?.data
        if let original {
            contents = String(decoding: original, as: UTF8.self)
        } else {
            guard allowNew else { throw Self.issue("找不到已配置的脚本。可以重新读取，或改用默认脚本。") }
            contents = Self.emptyTemplate
        }
    }

    mutating func save(_ edited: String) throws {
        guard !edited.contains("\0"), edited.utf8.count <= Self.maximumBytes else {
            throw Self.issue("脚本需要是文本，且不超过 256 KB。")
        }
        // A blank file or plain shell commands still form a runnable, no-op-by-default script.
        let normalized = edited.hasPrefix("#!") ? edited : "#!/bin/sh\n" + edited
        let data = Data(normalized.utf8)
        guard data.count <= Self.maximumBytes else { throw Self.issue("脚本不能超过 256 KB。") }
        let current = try Self.read(url)
        guard current?.data == original else {
            throw Self.issue("脚本已被其他编辑器修改，请重新读取后再保存。")
        }
        let permissions = ((current?.permissions ?? 0o700) & 0o777) | 0o100
        if data == original, current?.permissions == permissions {
            contents = normalized
            return
        }
        let manager = FileManager.default
        let directory = url.deletingLastPathComponent()
        try manager.createDirectory(at: directory, withIntermediateDirectories: true,
                                    attributes: [.posixPermissions: 0o700])
        let temporary = directory.appendingPathComponent(".hook-\(UUID().uuidString).tmp")
        defer { try? manager.removeItem(at: temporary) }
        try data.write(to: temporary, options: .withoutOverwriting)
        try manager.setAttributes([.posixPermissions: permissions], ofItemAtPath: temporary.path)
        guard try Self.read(url)?.data == original else {
            throw Self.issue("脚本已被其他编辑器修改，请重新读取后再保存。")
        }
        // Publish complete executable bytes atomically; creating a new file never replaces a rival file.
        let result = original == nil ? link(temporary.path, url.path) : rename(temporary.path, url.path)
        guard result == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        original = data
        contents = normalized
    }

    private static func read(_ url: URL) throws -> (data: Data, permissions: Int)? {
        let attributes: [FileAttributeKey: Any]
        do { attributes = try FileManager.default.attributesOfItem(atPath: url.path) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain &&
            [NSFileNoSuchFileError, NSFileReadNoSuchFileError].contains(error.code) {
            return nil
        }
        guard attributes[.type] as? FileAttributeType == .typeRegular,
              ((attributes[.size] as? NSNumber)?.intValue ?? Int.max) <= maximumBytes else {
            throw issue("当前 Hook 不是可编辑的文本脚本，或超过 256 KB。原程序会继续使用；也可以改用默认脚本。")
        }
        let data = try Data(contentsOf: url)
        guard data.count <= maximumBytes, let text = String(data: data, encoding: .utf8), !text.contains("\0") else {
            throw issue("当前 Hook 不是 UTF-8 文本脚本。原程序会继续使用；也可以改用默认脚本。")
        }
        return (data, (attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0o700)
    }

    private static func issue(_ message: String) -> NSError {
        NSError(domain: "AgenticFan", code: 3, userInfo: [NSLocalizedDescriptionKey: message])
    }
}
