import AppKit

// A status item is visible in other apps' full-screen Spaces, but its popover
// doesn't inherit that behavior. Configure the actual AppKit-created window.
final class MenuPopover: NSPopover, NSPopoverDelegate {
    private var previousActivationPolicy: NSApplication.ActivationPolicy?
    override init() {
        super.init()
        delegate = self
        behavior = .transient
        animates = false
    }

    required init?(coder: NSCoder) { super.init(coder: coder); delegate = self }

    override func show(relativeTo positioningRect: NSRect, of positioningView: NSView, preferredEdge: NSRectEdge) {
        // Regular foreground apps cannot overlay another app's full-screen Space.
        // Only borrow accessory behavior while presenting from an inactive Space;
        // the normal main-window/Dock behavior is restored when the popover closes.
        if NSApp.activationPolicy() == .regular && positioningView.window?.isOnActiveSpace == false {
            previousActivationPolicy = .regular
            NSApp.setActivationPolicy(.accessory)
        }
        super.show(relativeTo: positioningRect, of: positioningView, preferredEdge: preferredEdge)
        if !isShown { restoreActivationPolicy() }
    }

    func popoverDidClose(_ notification: Notification) { restoreActivationPolicy() }

    private func restoreActivationPolicy() {
        guard let previous = previousActivationPolicy else { return }
        previousActivationPolicy = nil
        NSApp.setActivationPolicy(previous)
    }

    func popoverWillShow(_ notification: Notification) { prepareWindow() }

    func popoverDidShow(_ notification: Notification) {
        guard previousActivationPolicy != nil else { return }
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
        guard previousActivationPolicy != nil, let window = contentViewController?.view.window else { return }
        window.collectionBehavior = [
            .canJoinAllSpaces, .fullScreenAuxiliary, .canJoinAllApplications,
            .transient, .ignoresCycle
        ]
    }
}
