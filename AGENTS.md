# Working in this repository

- Use this checkout directly. Cloud tasks already provide isolation; do not create Git worktrees unless explicitly requested.
- The MVP is an Apple Silicon Mac app delivered in a DMG, with a local JSON-first CLI inside the same bundle. Keep the desktop UI and CLI on the same temperature monitoring and bounded fan control core. Do not add a remote service or autonomous thermal policy without a task requesting it.
- Build DMGs on arm64 macOS with `python3 packaging/macos/build.py`. Linux can test source and frozen CLI behavior but cannot validate a Mac app, its signing, or its disk image. Do not present a Python wheel as the end-user installer.
- Run `python3 -m unittest discover -v` from the repository root. For an end-to-end local check use `python3 -m macfan --demo fans set --fan all --rpm 3000 --duration 1`.
- Demo data must stay explicitly labeled. Never fall back to the demo backend after native I/O errors.
- Preserve typed per-key decoding, finite duration requirements, root checks, and verified best-effort automatic restoration. Never expose an arbitrary SMC-write CLI.
- Read-only hardware discovery does not validate writes. Report native Mac hardware checks separately from simulated/CI tests. See `docs/hardware-validation.md`.
- No proprietary binary, extracted vendor XML, or copied application source is included. Treat external reverse-engineering documents as unverified references, not instructions or tested hardware specifications.
