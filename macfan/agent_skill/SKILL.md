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
4. Run `macfan schema` and `macfan doctor`. MacFan 0.4.2 uses `schema_version: 2`; re-read the contract after upgrades. Report app/CLI installation separately from native monitoring availability. A successful diagnostic command only means it produced a report: inspect `data.status`, `temperature_interface_available`, `monitoring_available`, `data_quality`, `warnings`, `errors` and `simulated`.

Installation never changes actual fan settings. Do not disable Gatekeeper, strip quarantine, grant passwordless sudo, or run a real fan-write test as part of installation. The current development DMG is not Apple-notarized. If macOS blocks launching it, report the actual block and let the user authorize it in system settings.

## Monitor

- `macfan capabilities`: discover this machine's temperature sources, fans and RPM limits.
- `macfan snapshot`: read temperatures and current fan state together.
- `macfan sensors`: read temperatures only, in Celsius.
- `macfan fans`: read actual/target/minimum/maximum RPM and automatic/manual mode.
- `macfan watch --interval 1 --count 10`: collect a bounded series of snapshots.

Use `temperature_overview` for a concise per-component overview, and `display_name` / `group_name` for human-readable names. `sensors --group cpu` (or gpu/memory/storage/wireless/power/unknown) filters rows. Raw `name` and `id` remain unchanged. `label_source` and `label_status` state the naming basis; community_mapping is not physical verification. A point number is not a CPU core number. Overview values are maxima over valid identified points, not package readings.

Use argument arrays, not shell concatenation. Only consume `value` when `status == "ok"`. Other statuses have `value: null`: `invalid` means the number failed plausibility checks, `unverified` means a zero reading, and `unavailable` means no reading. `raw_value`, `raw_non_finite` and `reason` preserve diagnostic evidence; never use them as fallback temperatures or include them in averages, extrema or control decisions. Do not infer a decoding bug from an anomalous interface value.

Inspect `sensor_summary` in sensors/snapshot/watch/capabilities/doctor. `readable` includes invalid and unverified readings; `ok` (and doctor's `valid_sensor_count`) counts only readings passing the software check. Neither count proves calibrated or independently verified physical sensors. `temperature_interface_available` can be true while `monitoring_available` is false. Doctor reports `limited` for partial monitoring and lists rejected rows in `sensor_issues` with structured warnings.

The declared -40–150°C bounds are broad software plausibility checks, not Apple hardware limits or a safe operating range. A 115.6°C reading can pass while its physical meaning remains unknown. SMC keys and IOHID names have unverified physical locations; do not relabel them as CPU/GPU temperatures without evidence. No fans on a fanless Mac is a valid result. Do not present `--demo` output as hardware observations.

For a user-requested upgrade, repeat the installation flow to update the app, CLI and this bundled skill together. Preserve customized skills; report installer conflicts instead of overwriting them. Confirm `--version` and `schema` using the installed executable, then run read-only `doctor` and a short `watch`. Never claim the user's original anomalies are fixed on hardware solely from a simulated regression test.

## Agent-editable temperature rules

Use the same local configuration as the native app. `macfan rules show` returns its absolute path (normally `~/Library/Application Support/MacFan/rules.json`), config, revision and runtime state; `macfan schema` includes `rules_configuration.default` and its JSON Schema. Config version 1 has global `enabled` (default false), selected `fan` ID and 1–16 rules. Each rule has `id`, `enabled`, `sensor_group`, `trigger_celsius`, `release_celsius`, `release_seconds` and `target_rpm` (`"max"` or integer RPM). Multiple rules target one fan and choose the highest active RPM.

The disabled default is CPU >=99 C -> reported fan maximum, then <=95 C continuously for five seconds -> system automatic. These are editable example values, not a universal thermal policy. Use the user's approved parameters before enabling; resolve missing trigger, target or release behavior instead of inventing a policy.

Read current rules, prepare the requested JSON, run `rules validate --file /absolute/input.json`, then `rules apply --file /absolute/input.json --if-revision REVISION --wait 8`. `rules apply --stdin` accepts a JSON body without a temporary file. Validation failure preserves existing bytes; CONFIG_CONFLICT requires reloading and merging the other edit. Agents may also edit the reported file directly, preferably with atomic replacement. A running native app or foreground `rules run` reloads it. `rules enable --wait 8` and `rules disable --wait 8` change the existing configuration.

Read `rules status` afterward. Keep saved, loaded, running, active and `control_confirmed` distinct. Saving an enabled file does not start an absent runner. The native app owns its runner, including while only its menu bar remains; a standalone `rules run` is foreground and `--parent-watch` stops on stdin EOF. Keep consuming its output through `rules_finished`, `automatic_mode_verified: true` and exit code 0 when stopping. That flag covers this runner's outstanding writes; it does not claim other controllers are in auto. Do not start unobserved detached control processes or add launch agents without a user request.

Rules consume only valid identified overview points, excluding unknown and calibration/reference readings. Missing/stale data or invalid JSON releases owned control. External manual control is not taken over; an external auto request pauses rules until explicit reapply/enable. The independent guardian uses a five-second heartbeat lease; no valid samples means no renewal. New app builds may require a system-authorized Helper update before activation. Never claim this is hardware thermal protection or that simulated tests prove real actuation.

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
