# Changelog

## Unreleased

- Fix curve tracking feedback in 0.4.5: report fresh hardware observations of the applied target independently of pending requests, expire stale feedback, and revoke confirmation when RPM drifts after an earlier success. Keep exact sequence confirmation and continuous mismatch deadlines. Show applied/actual RPM and tracking state in the native UI; stop retaining healthy feedback after control ends.

## 0.4.4 — 2026-10-31

- Add an editable temperature/RPM curve with draggable nodes, numeric add/edit/delete controls, linear interpolation, endpoint clamping and smooth time-based RPM changes. Native UI and CLI share revision-protected configuration; keep system automatic mode and existing threshold rules. Retarget the same leased guardian session, validate every node against fan limits, and tie confirmation to update sequences without extending control deadlines.

- Share Hook file discovery, atomic writes, independent revision checks, non-executing validation and status between the native editor and new `hook show/apply/validate/status` CLI commands. Execution receipts report file revisions observed before and after execution and remain available after the runner stops; update the machine-readable contract and bundled Agent skill.

- Make general-purpose threshold Hooks visible in the main window and menu bar, with a directly editable script file that starts as a no-op template. Save code without choosing a path or setting executable permissions; preserve existing text scripts, detect external edits, and show event JSON, timeout and execution status.
- Retry unsuccessful first-launch login-item registration on a later launch while preserving explicit and system opt-outs; explain that login startup resumes saved rules and custom Hooks.

- Simplify daily CI to one Python version and one core gate while retaining the macOS DMG build and installation contract.

- Standardize squash PR merging and automatic remote task-branch deletion while retaining local branches and worktrees for the existing archive workflow.

## 0.4.3 — 2026-10-25

- Keep control confirmation tied to the current fan and requested RPM when a higher-priority rule replaces an active target.

- Add continuously observed high-temperature trigger delays and opt-in fixed-program rule hooks with bounded asynchronous execution, status reporting and a dry-run-first Lark CLI example.

- Show the native app and menu bar with labeled demonstration data, make the signed DMG easier to find, and improve mobile installation text and link previews.

- Clarify the Agent installation prompt, current-Agent Skill selection and installed CLI path while retaining read-only hardware acceptance.

- Point the Clash Verge Monitor website link to its renamed canonical domain.

- Update the related-project website link label to Clash Verge Monitor.

- Correct the stale AgenticFan product name in website design QA documentation.

- Add the project MIT license, retain it in the app and DMG, and document the separate third-party code and font licenses.

## 0.4.2 — 2026-10-23

- Install the public stable Release anonymously, pin its downloads to one tag, and require Gatekeeper verification; retain explicit development builds and isolated installation checks.


- Refresh the product website with the timmyagentic visual style while preserving installation, download and documentation paths.

- Add the public AgenticFan website with installation guidance, compatibility limits and deployment configuration.

- Rename the product to AgenticFan and the repository to agentic-fan. The app, CLI, Python module, skill, configuration and Helper identifiers use the new name directly, without legacy aliases or migration.

Version **0.4.2** follows the previous release, 0.4.1.
The earlier 0.5.0/0.5.1 development version labels were never published and are
corrected here; existing release tags and historical measurements are unchanged.

### Added

- Shared, editable temperature rules with bounded control and automatic restoration.
- Login-item settings and continued monitoring when the main window is closed.
- Repository contribution, review, merge and release rules adapted from QuotaMonitor,
  a PR template, bilingual changelogs, and a CI check enforcing patch-sized updates.

### Fixed

- Unbounded temporary JSON retention and queued samples in native monitoring.
- Intermittent native model test termination and isolation of user rules during
  packaging smoke tests.

### Version policy

- Routine updates keep the published version or target its next patch. Minor and
  major increments require explicit user authorization recorded in the PR.
