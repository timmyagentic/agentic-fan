import Foundation
import AppKit

@main
struct HookDocumentTests {
    static func main() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let document = try decoder.decode(HookDocument.self, from: FileHandle.standardInput.readDataToEndOfFile())
        assert(document.script.editable && document.script.path.hasPrefix("/"))
        assert(document.script.content?.hasPrefix("#!/bin/sh") == true)
        let editor = HookCodeEditor.makeScrollView(text: document.script.content ?? "").documentView as! NSTextView
        assert(!editor.isRichText && !editor.isAutomaticQuoteSubstitutionEnabled && !editor.isAutomaticDashSubstitutionEnabled)
        assert(!editor.isAutomaticTextReplacementEnabled && !editor.isAutomaticSpellingCorrectionEnabled)
        let code = "printf '%s\\n' \"quoted--text\"\n"
        editor.insertText(code, replacementRange: NSRange(location: 0, length: editor.string.utf16.count))
        assert(editor.string == code, "Code punctuation must be preserved verbatim")
        let options = try document.applyOptions(enabled: document.settings.enabled, timeout: 15, useDefault: false)
        let output = try JSONSerialization.data(withJSONObject: ["apply_options": options])
        print(String(decoding: output, as: UTF8.self))
    }
}
