import Foundation

private final class FakePreferences: LoginItemPreferences {
    var defaultApplied = false
}

private final class FakeService: LoginItemService {
    var current = LoginItemState.notRegistered
    var reads = 0
    var registrations = 0
    var removals = 0
    var fails = false
    var state: LoginItemState { reads += 1; return current }
    func register() throws {
        registrations += 1
        if fails { throw NSError(domain: "test", code: 1) }
        current = .enabled
    }
    func unregister() throws { removals += 1; current = .notRegistered }
}

@main
struct LoginItemTests {
    static func main() {
        let service = FakeService(), preferences = FakePreferences()
        let settings = LoginItemSettings(service: service, preferences: preferences, demo: false)
        settings.configureDefault()
        assert(settings.enabled && preferences.defaultApplied && service.registrations == 1)
        settings.configureDefault()
        assert(service.registrations == 1, "Default must only register once")
        service.current = .requiresApproval
        settings.configureDefault()
        assert(!settings.enabled && settings.state == .requiresApproval && service.registrations == 1)
        service.current = .notRegistered
        settings.configureDefault()
        assert(!settings.enabled && service.registrations == 1)
        settings.setEnabled(true)
        assert(settings.enabled && service.registrations == 2)
        settings.setEnabled(false)
        settings.configureDefault()
        assert(!settings.enabled && service.removals == 1 && service.registrations == 2)
        for initial in [LoginItemState.enabled, .requiresApproval, .notFound] {
            let existing = FakeService(), saved = FakePreferences()
            existing.current = initial
            LoginItemSettings(service: existing, preferences: saved, demo: false).configureDefault()
            assert(existing.registrations == 0 && saved.defaultApplied)
        }
        let failing = FakeService(), attempted = FakePreferences()
        failing.fails = true
        let failed = LoginItemSettings(service: failing, preferences: attempted, demo: false)
        failed.configureDefault(); failed.configureDefault()
        assert(failing.registrations == 1 && failed.error != nil && !failed.enabled)
        failing.fails = false
        failed.setEnabled(true)
        assert(failed.enabled && failed.error == nil)
        let isolated = FakeService(), untouched = FakePreferences()
        let demo = LoginItemSettings(service: isolated, preferences: untouched, demo: true)
        demo.configureDefault(); demo.setEnabled(true); demo.setEnabled(false); demo.refresh()
        assert(isolated.reads == 0 && isolated.registrations == 0 && isolated.removals == 0)
        assert(!untouched.defaultApplied, "Demo must not suppress the real first-launch default")
        print("PASS: login default, explicit opt-out, system opt-out, failure and demo isolation")
    }
}
