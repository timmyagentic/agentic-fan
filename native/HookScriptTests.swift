import Foundation
import AppKit

@main
struct HookScriptTests {
    static func reject(_ action: () throws -> Void) {
        do { try action(); fatalError("Expected an edit conflict or invalid file to be rejected") }
        catch { }
    }

    static func main() throws {
        let editor = HookCodeEditor.makeScrollView(text: "").documentView as! NSTextView
        assert(!editor.isRichText && !editor.isAutomaticQuoteSubstitutionEnabled && !editor.isAutomaticDashSubstitutionEnabled)
        assert(!editor.isAutomaticTextReplacementEnabled && !editor.isAutomaticSpellingCorrectionEnabled)
        let code = "printf '%s\\n' \"quoted--text\"\n"
        editor.insertText(code, replacementRange: NSRange(location: 0, length: 0))
        assert(editor.string == code, "The editor must preserve code punctuation verbatim")
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
        let manager = FileManager.default
        let url = HookScript.defaultURL(rulesPath: root.appendingPathComponent("rules.json").path)
        assert(url == root.appendingPathComponent("hooks/threshold-hook.sh"))
        var script = try HookScript(url: url, allowNew: true)
        assert(!script.exists && script.contents == HookScript.emptyTemplate)
        assert(!manager.fileExists(atPath: url.path), "Opening an unsaved draft must not write a file")
        try script.save(script.contents)
        assert(script.exists && manager.isExecutableFile(atPath: url.path))
        assert(try! String(contentsOf: url, encoding: .utf8) == HookScript.emptyTemplate)
        try script.save("cat > event.json\n")
        assert(script.contents == "#!/bin/sh\ncat > event.json\n")
        let reopened = try HookScript(url: url, allowNew: true)
        assert(reopened.contents == script.contents, "Opening the editor must preserve saved code")

        var noop = try HookScript(url: root.appendingPathComponent("noop.sh"), allowNew: true)
        try noop.save(HookScript.emptyTemplate)
        var blank = try HookScript(url: root.appendingPathComponent("blank.sh"), allowNew: true)
        try blank.save("")
        assert(blank.contents == "#!/bin/sh\n")

        let existingURL = root.appendingPathComponent("existing.py")
        let original = "#!/usr/bin/env python3\nprint('old')\n"
        try original.write(to: existingURL, atomically: true, encoding: .utf8)
        try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: existingURL.path)
        var existing = try HookScript(url: existingURL, allowNew: false)
        assert(existing.url == existingURL && existing.contents == original)
        let changed = "#!/usr/bin/env python3\nprint('updated')\n"
        try existing.save(changed)
        assert(existing.contents == changed && manager.isExecutableFile(atPath: existingURL.path))
        assert((try! manager.attributesOfItem(atPath: existingURL.path)[.posixPermissions] as? NSNumber)?.intValue == 0o700)
        try "external edit\n".write(to: existingURL, atomically: true, encoding: .utf8)
        reject { try existing.save("do not overwrite") }
        assert(try! String(contentsOf: existingURL, encoding: .utf8) == "external edit\n")

        let rivalURL = root.appendingPathComponent("rival.sh")
        var rival = try HookScript(url: rivalURL, allowNew: true)
        try "other writer".write(to: rivalURL, atomically: true, encoding: .utf8)
        reject { try rival.save("replace") }
        assert(try! String(contentsOf: rivalURL, encoding: .utf8) == "other writer")
        reject { _ = try HookScript(url: root.appendingPathComponent("missing.sh"), allowNew: false) }
        reject { _ = try HookScript(url: root, allowNew: false) }
        let symlink = root.appendingPathComponent("linked.sh")
        try manager.createSymbolicLink(at: symlink, withDestinationURL: url)
        reject { _ = try HookScript(url: symlink, allowNew: false) }
        let binary = root.appendingPathComponent("binary")
        try Data([0, 255, 0]).write(to: binary)
        reject { _ = try HookScript(url: binary, allowNew: false) }
        reject { try script.save("bad\0code") }
        reject { try script.save(String(repeating: "x", count: HookScript.maximumBytes)) }
        assert(try! String(contentsOf: url, encoding: .utf8) == reopened.contents)
        print("PASS: blank template, executable persistence, existing scripts, edit conflicts and non-text protection")
    }
}
