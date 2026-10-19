# Changelog

## Unreleased

The next release is **0.4.2**, following the latest published release, 0.4.1.
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
