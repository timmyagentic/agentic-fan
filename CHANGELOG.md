# Changelog

## Unreleased

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
