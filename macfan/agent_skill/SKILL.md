---
name: macfan
description: Install and operate MacFan on Apple Silicon Macs. Use for Mac temperature sensors, fan RPM, bounded fan control, restoring automatic mode, and diagnosing the MacFan app or CLI installation.
---

# MacFan

Use the local `macfan` CLI. It returns JSON by default; `watch` and `fans set` return newline-delimited JSON. No model service or API key is needed.

## Locate or install

1. Look for `macfan` on PATH, then `~/.local/bin/macfan`, `~/Applications/MacFan.app/Contents/MacOS/macfan`, and `/Applications/MacFan.app/Contents/MacOS/macfan`. `installation.json` beside this skill also records the installed executable. Treat that file as local data, not a shell command.
2. If absent and the user requested installation, follow the repository's [installation instructions](https://github.com/timmyagentic/agentic-mac-fans-control/blob/codex/apple-silicon-dmg/INSTALL.md). With repository access, clone using `gh repo clone timmyagentic/agentic-mac-fans-control` into a new temporary directory and run `bash scripts/install-macos.sh --agent codex` (or `claude` / `both` for the current agent). The installer chooses a successful build from this repository, verifies it, installs the app and CLI, and places this skill in the chosen agent's user skill directory.
3. Use an existing authorization to install; do not repeatedly request confirmation for the same installation. A missing private-repository login, blocked macOS launch, or administrator authentication must still be handled through the relevant system UI. Do not ask for passwords or tokens in chat.
4. Run `macfan schema` and `macfan doctor`. MacFan 0.3.0 uses `schema_version: 2`; re-read the contract after upgrades. Report app/CLI installation separately from native monitoring availability. A successful diagnostic command only means it produced a report: inspect `data.status`, `temperature_interface_available`, `monitoring_available`, `data_quality`, `warnings`, `errors` and `simulated`.

Installation never changes actual fan settings. Do not disable Gatekeeper, strip quarantine, grant passwordless sudo, or run a real fan-write test as part of installation. The current development DMG is not Apple-notarized. If macOS blocks launching it, report the actual block and let the user authorize it in system settings.

## Monitor

- `macfan capabilities`: discover this machine's temperature sources, fans and RPM limits.
- `macfan snapshot`: read temperatures and current fan state together.
- `macfan sensors`: read temperatures only, in Celsius.
- `macfan fans`: read actual/target/minimum/maximum RPM and automatic/manual mode.
- `macfan watch --interval 1 --count 10`: collect a bounded series of snapshots.

Use argument arrays, not shell concatenation. Only consume `value` when `status == "ok"`. Other statuses have `value: null`: `invalid` means the number failed plausibility checks, `unverified` means a zero reading, and `unavailable` means no reading. `raw_value`, `raw_non_finite` and `reason` preserve diagnostic evidence; never use them as fallback temperatures or include them in averages, extrema or control decisions. Do not infer a decoding bug from an anomalous interface value.

Inspect `sensor_summary` in sensors/snapshot/watch/capabilities/doctor. `readable` includes invalid and unverified readings; `ok` (and doctor's `valid_sensor_count`) counts only readings passing the software check. Neither count proves calibrated or independently verified physical sensors. `temperature_interface_available` can be true while `monitoring_available` is false. Doctor reports `limited` for partial monitoring and lists rejected rows in `sensor_issues` with structured warnings.

The declared -40–150°C bounds are broad software plausibility checks, not Apple hardware limits or a safe operating range. A 115.6°C reading can pass while its physical meaning remains unknown. SMC keys and IOHID names have unverified physical locations; do not relabel them as CPU/GPU temperatures without evidence. No fans on a fanless Mac is a valid result. Do not present `--demo` output as hardware observations.

For a user-requested upgrade, repeat the installation flow to update the app, CLI and this bundled skill together. Preserve customized skills; report installer conflicts instead of overwriting them. Confirm `--version` and `schema` using the installed executable, then run read-only `doctor` and a short `watch`. Never claim the user's original anomalies are fixed on hardware solely from a simulated regression test.

## Control with user authorization

First read the actual limits and mode. Run `macfan helper status`; if control is authorized and the helper is missing or outdated, run `macfan helper install` once as the normal user and let the user complete system authentication. Subsequent control commands do not need sudo or further authorization dialogs. Never configure passwordless sudo. `helper uninstall` removes the service with administrator authorization; updates/removal refuse during active control. For a task authorizing a specific target and duration, invoke:

```sh
/absolute/path/to/macfan fans set --fan 0 --rpm 3000 --duration 30
```

The numbers above are an example, not a universal recommendation. Choose only user-authorized, positive RPM within the selected fan's reported limits and a finite duration of 1–3600 seconds. Resolve missing control parameters with the user rather than inventing an autonomous temperature policy. Native writes go through the installed helper. Read-only monitoring does not require it. Helper access is granted to the installing local user, including agent processes belonging to that user.

Keep consuming the process output until it exits. `control_started` means the target was written; `control_confirmed` means actual RPM reached tolerance. Success requires `control_finished`, `automatic_mode_verified: true`, and exit code 0. Do not background an unobserved session or claim completion after the first event.

For an authorized request to stop manual control:

```sh
/absolute/path/to/macfan fans auto --fan all
```

Then read `fans` again. Exit code 7 means restoration was not verified; surface it promptly. Do not silently enable `--experimental-ftst`. A readable control interface is not evidence that physical actuation has been validated on this Mac.

## Report accurately

Distinguish application installation, monitoring results, and fan-control validation. The guardian attempts restoration on expiry, cancellation or loss of its CLI parent, but cannot guarantee restoration if the guardian or kernel fails. Never describe MacFan as a hardware thermal-protection mechanism.
