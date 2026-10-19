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
        settings.setEnabled(true)
        assert(service.registrations == 2, "Enabling an enabled login item is idempotent")
        settings.setEnabled(false)
        settings.setEnabled(false)
        settings.configureDefault()
        assert(!settings.enabled && service.removals == 1 && service.registrations == 2)
        for initial in [LoginItemState.enabled, .requiresApproval] {
            let existing = FakeService(), saved = FakePreferences()
            existing.current = initial
            LoginItemSettings(service: existing, preferences: saved, demo: false).configureDefault()
            assert(existing.registrations == 0 && saved.defaultApplied)
        }
        let missingRecord = FakeService(), newPreferences = FakePreferences()
        missingRecord.current = .notFound
        let fresh = LoginItemSettings(service: missingRecord, preferences: newPreferences, demo: false)
        fresh.configureDefault()
        assert(fresh.enabled && missingRecord.registrations == 1,
               "A main app without a BTM record still needs first-launch registration")

        for operation in ["status", "enable", "disable"] {
            assert(loginItemOperation(["--login-item", operation]) == operation)
            assert(loginItemOperation(["--demo", "--login-item", operation]) == operation)
        }
        for invalid in [[String](), ["--login-item"], ["--login-item", "wrong"],
                        ["--typo", "--login-item", "disable"], ["--login-item", "status", "extra"],
                        ["--demo", "--demo", "--login-item", "enable"],
                        ["--smoke", "--login-item", "enable"]] {
            assert(loginItemOperation(invalid) == nil, "Invalid arguments must never reach a service")
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
        assert(demo.command("status") && demo.command("enable") && demo.command("disable"))
        assert(!demo.command("invalid") && isolated.reads == 0 && !untouched.defaultApplied)
        print("PASS: login default, explicit opt-out, system opt-out, failure and demo isolation")
    }
}
