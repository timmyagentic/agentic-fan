---
name: agenticfan
description: Install and operate AgenticFan on Apple Silicon Macs. Use for Mac temperature sensors, fan RPM, bounded fan control, restoring automatic mode, custom temperature-rule Hook scripts, and diagnosing the AgenticFan app or CLI installation.
---

# AgenticFan

Use the local `agenticfan` CLI. It returns JSON by default; `watch` and `fans set` return newline-delimited JSON. No model service or API key is needed.

## Locate or install

1. Look for `agenticfan` on PATH, then `~/.local/bin/agenticfan`, `~/Applications/AgenticFan.app/Contents/MacOS/agenticfan`, and `/Applications/AgenticFan.app/Contents/MacOS/agenticfan`. `installation.json` beside this skill also records the installed executable. Treat that file as local data, not a shell command.
2. If absent and the user requested installation, follow the repository's [installation instructions](https://github.com/timmyagentic/agentic-fan/blob/codex/apple-silicon-dmg/INSTALL.md). Download and read `install-macos.sh` from the public latest Release in a new temporary directory, then run `bash install-macos.sh --agent codex` (or `claude` / `both`). The default installer anonymously resolves one stable Release, pins its tag, verifies the DMG checksum, app signature and Gatekeeper, and installs the app, CLI and skill. GitHub CLI authentication is needed only for explicit `--run-id` development builds.
3. Use an existing authorization to install; do not repeatedly request confirmation for the same installation. A blocked macOS launch or administrator authentication must still be handled through the relevant system UI. Do not ask for passwords or tokens in chat.
4. Run `agenticfan schema` and `agenticfan doctor`. AgenticFan 0.4.4 uses `schema_version: 2`; re-read the contract after upgrades. Report app/CLI installation separately from native monitoring availability. A successful diagnostic command only means it produced a report: inspect `data.status`, `temperature_interface_available`, `monitoring_available`, `data_quality`, `warnings`, `errors` and `simulated`.

Installation never changes actual fan settings. Do not disable Gatekeeper, strip quarantine, grant passwordless sudo, or run a real fan-write test as part of installation. The current development DMG is not Apple-notarized. If macOS blocks launching it, report the actual block and let the user authorize it in system settings.

## Monitor

- `agenticfan capabilities`: discover this machine's temperature sources, fans and RPM limits.
- `agenticfan snapshot`: read temperatures and current fan state together.
- `agenticfan sensors`: read temperatures only, in Celsius.
- `agenticfan fans`: read actual/target/minimum/maximum RPM and automatic/manual mode.
- `agenticfan watch --interval 1 --count 10`: collect a bounded series of snapshots.

Use `temperature_overview` for a concise per-component overview, and `display_name` / `group_name` for human-readable names. `sensors --group cpu` (or gpu/memory/storage/wireless/power/unknown) filters rows. Raw `name` and `id` remain unchanged. `label_source` and `label_status` state the naming basis; community_mapping is not physical verification. A point number is not a CPU core number. Overview values are maxima over valid identified points, not package readings.

Use argument arrays, not shell concatenation. Only consume `value` when `status == "ok"`. Other statuses have `value: null`: `invalid` means the number failed plausibility checks, `unverified` means a zero reading, and `unavailable` means no reading. `raw_value`, `raw_non_finite` and `reason` preserve diagnostic evidence; never use them as fallback temperatures or include them in averages, extrema or control decisions. Do not infer a decoding bug from an anomalous interface value.

Inspect `sensor_summary` in sensors/snapshot/watch/capabilities/doctor. `readable` includes invalid and unverified readings; `ok` (and doctor's `valid_sensor_count`) counts only readings passing the software check. Neither count proves calibrated or independently verified physical sensors. `temperature_interface_available` can be true while `monitoring_available` is false. Doctor reports `limited` for partial monitoring and lists rejected rows in `sensor_issues` with structured warnings.

The declared -40–150°C bounds are broad software plausibility checks, not Apple hardware limits or a safe operating range. A 115.6°C reading can pass while its physical meaning remains unknown. SMC keys and IOHID names have unverified physical locations; do not relabel them as CPU/GPU temperatures without evidence. No fans on a fanless Mac is a valid result. Do not present `--demo` output as hardware observations.

For a user-requested upgrade, repeat the installation flow to update the app, CLI and this bundled skill together. Preserve customized skills; report installer conflicts instead of overwriting them. Confirm `--version` and `schema` using the installed executable, then run read-only `doctor` and a short `watch`. Never claim the user's original anomalies are fixed on hardware solely from a simulated regression test.

## Agent-editable temperature rules

The same configuration also supports an optional `mode: "curve"` with `curve: {"sensor_group":"cpu","ramp_rpm_per_second":200,"nodes":[{"temperature_celsius":60,"target_rpm":1200},{"temperature_celsius":100,"target_rpm":4500}]}`. These are examples, not approved settings or Apple thermal limits. Use 2–16 nodes with strictly increasing temperatures and nondecreasing integer RPM. Every node must fit the selected fan's actual limits. Missing mode preserves threshold rules; `enabled:false` restores owned control to system automatic mode and retains saved nodes. Never enable a new curve or migrate the user's enabled rules without their authorization.

Curves interpolate linearly, hold endpoint RPM outside the node range, and slew from observed fan RPM by at most `ramp_rpm_per_second` (1–1000, default 200) per second. `curve_target_rpm` is the interpolated goal; `desired_rpm` is the smoothed request. Updates use the same finite leased guardian session without automatic-mode toggles between targets; exact update sequences prevent old confirmations from confirming new targets. Retargeting extends neither deadline nor heartbeat lease. Missing/stale data, invalid config and stop still release owned control. Curve mode retains but does not evaluate threshold rules or dispatch their Hooks. Physical RPM response, cooling and sound require separate hardware acceptance.

Use the same local configuration as the native app. `agenticfan rules show` returns its absolute path (normally `~/Library/Application Support/AgenticFan/rules.json`), config, revision and runtime state; `agenticfan schema` includes `rules_configuration.default` and its JSON Schema. Config version 1 has global `enabled` (default false), selected `fan` ID and 1–16 rules. Each rule has `id`, `enabled`, `sensor_group`, `trigger_celsius`, `release_celsius`, `release_seconds` and `target_rpm` (`"max"` or integer RPM). Multiple rules target one fan and choose the highest active RPM.

The disabled default is CPU >=99 C -> reported fan maximum, then <=95 C continuously for five seconds -> system automatic. These are editable example values, not a universal thermal policy. Use the user's approved parameters before enabling; resolve missing trigger, target or release behavior instead of inventing a policy.

Optional per-rule `trigger_seconds` (0–300, default 0) requires continuously observed high temperature before activation. A below-trigger or missing/invalid sample, or a gap over three seconds, resets the hot timer. `pending` and `rule_states[].hot_seconds` expose this wait. Unchanged rules keep timers and activation across config reloads; changed rules and a restarted runner start fresh.

Optional global `hook` is null/absent by default, or `{"program":"/absolute/path/to/executable","timeout_seconds":10}`. It explicitly authorizes that local program to run as the current user when a rule becomes active. It receives one newline-terminated JSON `rule_triggered` event on stdin; no shell or dynamic arguments are used. Events include an ID, rule, temperature, requested target, control state and simulated flag; activation is not proof of physical actuation. One worker, a 16-event queue and a 0.1–60 second timeout isolate hooks from control. A full queue drops new events; failures appear under `hook` in `rules status`, never retried automatically. Equivalent config rewrites do not resend; restarting the runner can produce a new event after a full hot interval. Disabling rules/changing the hook cancels pending/running jobs. Stopping restores owned control before hook cleanup. Explicit demo hooks receive `simulated:true`; notification adapters must suppress real sends for demo events. Configure trusted programs only; keep credentials outside the rules JSON. Confirm recipient, sender identity and message template before enabling a real messaging hook.

Read current rules, prepare the requested JSON, run `rules validate --file /absolute/input.json`, then `rules apply --file /absolute/input.json --if-revision REVISION --wait 8`. `rules apply --stdin` accepts a JSON body without a temporary file. Validation failure preserves existing bytes; CONFIG_CONFLICT requires reloading and merging the other edit. Agents may also edit the reported file directly, preferably with atomic replacement. A running native app or foreground `rules run` reloads it. `rules enable --wait 8` and `rules disable --wait 8` change the existing configuration.

Read `rules status` afterward. Keep saved, loaded, running, active and `control_confirmed` distinct. Saving an enabled file does not start an absent runner. The native app owns its runner, including while only its menu bar remains; a standalone `rules run` is foreground and `--parent-watch` stops on stdin EOF. Keep consuming its output through `rules_finished`, `automatic_mode_verified: true` and exit code 0 when stopping. That flag covers this runner's outstanding writes; it does not claim other controllers are in auto. Do not start unobserved detached control processes or add launch agents without a user request.

Rules consume only valid identified overview points, excluding unknown and calibration/reference readings. Missing/stale data or invalid JSON releases owned control. External manual control is not taken over; an external auto request pauses rules until explicit reapply/enable. The independent guardian uses a five-second heartbeat lease; no valid samples means no renewal. New app builds may require a system-authorized Helper update before activation. Never claim this is hardware thermal protection or that simulated tests prove real actuation.

## Agent-editable Hook script files

AgenticFan 0.4.4 adds `hook show`, `hook apply`, `hook validate` and `hook status`. Verify the installed version and `schema` first; upgrade the CLI/app/bundled skill together before using these commands. The native editor invokes these same CLI operations. Do not use GUI automation to edit the script when these commands are available.

1. Run `agenticfan hook show` (optionally `--config /absolute/rules.json`). It returns `script.path`, `content`, `exists`, `editable`, `executable`, byte `revision`, and `settings.rules_revision`, enablement and timeout. For a new profile it reports the fixed `hooks/threshold-hook.sh` path beside the rules file and a no-action template without creating it. The missing-file revision is the literal `missing`. Existing UTF-8 scripts are edited at their original path. `--default` selects the managed file explicitly; selection alone does not switch an existing Hook.
2. Edit the returned content or prepare a local draft. Input to `hook apply/validate --stdin` and `--file` is raw UTF-8 script code, **not JSON**. Preserve the user's intended interpreter and business logic. Shebang-free or empty drafts receive `#!/bin/sh`; save sets owner execute permission. Binary, linked and oversized files are not replaced as text; report their `script.issue` or use an explicitly requested default-file replacement.
3. Run `hook validate --file /absolute/draft.sh` or `hook validate --stdin`. Validation never executes the Hook or its custom interpreter. Saved-file validation without input also checks existence, permissions and interpreter availability. Fixed system sh/bash use no-execution parsing; Python uses this CLI's AST grammar. Unsupported syntax is explicitly `syntax.status: unsupported`; inspect warnings, including environment-dependent interpreter lookup. Validation does not prove runtime dependencies, external-service access or business effects. Invalid checks return exit 2 with `error.details.validation`.
4. Save with both observed revisions:

```sh
agenticfan hook apply --file /absolute/draft.sh \
  --if-revision <script.revision> \
  --if-rules-revision <settings.rules_revision>
```

`--stdin` can replace `--file`. This preserves existing Hook enablement and the global temperature-rule switch. If the user authorized activation, add `--enable --timeout-seconds 15 --wait 8`; use the user's requested timeout. Enabling an already saved file without changing content also requires both revisions. `hook apply --disable --if-rules-revision <revision>` removes the Hook setting but retains the file and other rules; no script revision is required for disabling without a content write. Do not turn on temperature rules implicitly. Editing an already enabled script changes the code used by the next event processed by the runner, even if its rules JSON is unchanged.

On `HOOK_CONFLICT` or `CONFIG_CONFLICT`, reread, merge and retry; do not force-overwrite. `HOOK_BUSY` means another writer is active. Script and rule files are separate: `HOOK_APPLY_PARTIAL` reports exactly which writes succeeded; reread both instead of assuming rollback. The receipt includes `script_saved_revision`, `rules_revision`, `rules_generation`, `settings_saved` and `rules_loaded`. A settings load is not a script execution. Apply never runs a test event.

5. Run `hook validate` against the saved file, then `hook status`. Status omits code, and returns validation, `execution.runner_running`, runner state, settings loading and `last_result`. Check `script.revision` against the apply receipt. `last_result` includes the program, exit result, timestamps, duration, and `script_revision`/`script_revision_after` observed around execution. `last_observed_revision_matches_current` is null without comparable evidence; true is not proof of business success or an immutable execution snapshot. External editors may race, and unsupported/large executables may have no revision observation. Report saved, validation, running and actual execution separately.

Keep default scripts side-effect-free. Do not lower real temperature thresholds or execute an arbitrary Hook merely to test setup. A synthetic event flag is input data, not a sandbox; custom code can still perform real actions. Use only authorized destinations and actions when arranging an explicit end-to-end test. `rules validate` checks rule JSON structure; it does not replace `hook validate`.

## Control with user authorization

First read the actual limits and mode. Run `agenticfan helper status`; if control is authorized and the helper is missing or outdated, run `agenticfan helper install` once as the normal user and let the user complete system authentication. Subsequent control commands do not need sudo or further authorization dialogs. Never configure passwordless sudo. `helper uninstall` removes the service with administrator authorization; updates/removal refuse during active control. For a task authorizing a specific target and duration, invoke:

```sh
/absolute/path/to/agenticfan fans set --fan 0 --rpm 3000 --duration 30
```

The numbers above are an example, not a universal recommendation. Choose only user-authorized, positive RPM within the selected fan's reported limits and a finite duration of 1–3600 seconds. Resolve missing control parameters with the user rather than inventing an autonomous temperature policy. Native writes go through the installed helper. Read-only monitoring does not require it. Helper access is granted to the installing local user, including agent processes belonging to that user.

Keep consuming the process output until it exits. `control_started` means the target was written; `control_confirmed` means actual RPM reached tolerance. Success requires `control_finished`, `automatic_mode_verified: true`, and exit code 0. Do not background an unobserved session or claim completion after the first event.

For an authorized request to stop manual control:

```sh
/absolute/path/to/agenticfan fans auto --fan all
```

Then read `fans` again. Exit code 7 means restoration was not verified; surface it promptly. Do not silently enable `--experimental-ftst`. A readable control interface is not evidence that physical actuation has been validated on this Mac.

## Report accurately

Distinguish application installation, monitoring results, and fan-control validation. The guardian attempts restoration on expiry, cancellation or loss of its CLI parent, but cannot guarantee restoration if the guardian or kernel fails. Never describe AgenticFan as a hardware thermal-protection mechanism.
