"""Subprocess entry points for both Python development and a frozen Mac app."""
from __future__ import annotations

import sys


def cli_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, "-m", "agenticfan", *arguments]


def guardian_command() -> list[str]:
    if getattr(sys, "frozen", False):
        # A bundled executable is not a Python interpreter: it cannot accept -m.
        return [sys.executable, "--internal-guardian"]
    return [sys.executable, "-m", "agenticfan.guardian"]
