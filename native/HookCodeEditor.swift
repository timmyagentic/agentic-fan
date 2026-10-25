import AppKit
import SwiftUI

struct HookCodeEditor: NSViewRepresentable {
    @Binding var text: String
    @Environment(\.isEnabled) private var enabled

    static func makeScrollView(text: String) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        let editor = scroll.documentView as! NSTextView
        editor.isRichText = false
        editor.importsGraphics = false
        editor.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticDashSubstitutionEnabled = false
        editor.isAutomaticTextReplacementEnabled = false
        editor.isAutomaticSpellingCorrectionEnabled = false
        editor.isAutomaticTextCompletionEnabled = false
        editor.isAutomaticLinkDetectionEnabled = false
        editor.isAutomaticDataDetectionEnabled = false
        editor.isContinuousSpellCheckingEnabled = false
        editor.isGrammarCheckingEnabled = false
        editor.smartInsertDeleteEnabled = false
        editor.enabledTextCheckingTypes = 0
        editor.allowsUndo = true
        editor.textContainerInset = NSSize(width: 4, height: 4)
        editor.string = text
        editor.setAccessibilityLabel("Hook 脚本代码")
        return scroll
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = Self.makeScrollView(text: text)
        (scroll.documentView as? NSTextView)?.delegate = context.coordinator
        return scroll
    }
    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let editor = scroll.documentView as? NSTextView else { return }
        context.coordinator.text = $text
        editor.isEditable = enabled
        if editor.string != text {
            let cursor = min(editor.selectedRange().location, text.utf16.count)
            editor.string = text
            editor.setSelectedRange(NSRange(location: cursor, length: 0))
        }
    }
    func makeCoordinator() -> Coordinator { Coordinator($text) }
    final class Coordinator: NSObject, NSTextViewDelegate {
        var text: Binding<String>
        init(_ text: Binding<String>) { self.text = text }
        func textDidChange(_ notification: Notification) {
            if let editor = notification.object as? NSTextView { text.wrappedValue = editor.string }
        }
    }
}
