import AppKit

// A status item is visible in other apps' full-screen Spaces, but its popover
// doesn't inherit that behavior. Configure the actual AppKit-created window.
final class MenuPopover: NSPopover, NSPopoverDelegate {
    private var presentsAcrossSpaces = false
    override init() {
        super.init()
        delegate = self
        behavior = .transient
        animates = false
    }

    required init?(coder: NSCoder) { super.init(coder: coder); delegate = self }

    override func show(relativeTo positioningRect: NSRect, of positioningView: NSView, preferredEdge: NSRectEdge) {
        // The app stays accessory. Only detach the panel for another Space.
        presentsAcrossSpaces = positioningView.window?.isOnActiveSpace == false
        super.show(relativeTo: positioningRect, of: positioningView, preferredEdge: preferredEdge)
        if !isShown { presentsAcrossSpaces = false }
    }

    func popoverWillShow(_ notification: Notification) { prepareWindow() }

    func popoverDidShow(_ notification: Notification) {
        guard presentsAcrossSpaces else { return }
        // On the first presentation AppKit may create the window after willShow.
        // The status item's backing window can remain in the desktop Space even
        // when its button appears in a full-screen menu bar. A child window inherits
        // that Space, so let this nonactivating popover panel float independently.
        if let window = contentViewController?.view.window {
            window.parent?.removeChildWindow(window)
        }
        prepareWindow()
        contentViewController?.view.window?.orderFrontRegardless()
    }

    private func prepareWindow() {
        guard presentsAcrossSpaces, let window = contentViewController?.view.window else { return }
        window.collectionBehavior = [
            .canJoinAllSpaces, .fullScreenAuxiliary, .canJoinAllApplications,
            .transient, .ignoresCycle
        ]
    }
}
