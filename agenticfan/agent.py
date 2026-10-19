"""Install the bundled Agent skill and a user-local CLI entry point."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile

from . import __version__
from .errors import AgenticFanError, invalid

SKILL = Path(__file__).with_name("agent_skill") / "SKILL.md"
START = "# >>> agenticfan PATH >>>"
END = "# <<< agenticfan PATH <<<"


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".agenticfan-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def skill_directories(user_root: Path, target: str):
    if target not in ("codex", "claude", "both"):
        raise invalid("Choose codex, claude, or both.")
    folders = []
    if target in ("codex", "both"):
        folders.append(user_root / ".agents/skills/agenticfan")
    if target in ("claude", "both"):
        folders.append(user_root / ".claude/skills/agenticfan")
    return folders


def conflict(path):
    return AgenticFanError("INSTALL_CONFLICT", "An existing file is not managed by AgenticFan; it was not overwritten.",
                       6, path=str(path))


def integrate(executable: Path, user_root: Path, target="codex") -> dict:
    """Preflight every destination before writing; retain original shell profiles."""
    executable = executable.resolve(strict=True)
    if not os.access(executable, os.X_OK):
        raise invalid("The bundled CLI is not executable.", path=str(executable))
    user_root = user_root.expanduser().resolve()
    binary = user_root / ".local/bin/agenticfan"
    skill = SKILL.read_bytes()
    digest = hashlib.sha256(skill).hexdigest()
    destinations = skill_directories(user_root, target)
    receipt = json.dumps({"managed_by": "agenticfan", "version": __version__, "executable": str(executable),
                          "skill_sha256": digest}, indent=2).encode() + b"\n"
    writes = []
    profiles = []

    if binary.is_symlink():
        if binary.resolve() != executable:
            raise conflict(binary)
    elif binary.exists():
        raise conflict(binary)

    for directory in destinations:
        if directory.is_symlink():
            raise conflict(directory)
        if directory.exists() and any(directory.iterdir()):
            marker = directory / "installation.json"
            installed = directory / "SKILL.md"
            if marker.is_symlink() or installed.is_symlink():
                raise conflict(directory)
            try:
                record = json.loads(marker.read_text())
                if not isinstance(record, dict):
                    raise ValueError("Malformed installation receipt")
                unchanged = hashlib.sha256(installed.read_bytes()).hexdigest() == record["skill_sha256"]
            except (OSError, ValueError, KeyError):
                unchanged = False
                record = {}
            if record.get("managed_by") != "agenticfan" or not unchanged:
                raise conflict(directory)
        writes.extend(((directory / "SKILL.md", skill), (directory / "installation.json", receipt)))

    quoted = shlex.quote(str(binary.parent))
    block = f'{START}\ncase ":${{PATH}}:" in\n  *:{quoted}:*) ;;\n  *) export PATH={quoted}:"${{PATH}}" ;;\nesac\n{END}\n'
    for name in (".zprofile", ".bash_profile"):
        profile = user_root / name
        if profile.is_symlink():
            raise conflict(profile)
        previous = profile.read_bytes() if profile.exists() else b""
        text = previous.decode("utf-8")
        if START in text or END in text:
            if block not in text or text.count(START) != 1 or text.count(END) != 1:
                raise conflict(profile)
            continue
        backup = profile.with_name(name + ".before-agenticfan")
        if profile.exists() and not backup.exists():
            if backup.is_symlink():
                raise conflict(backup)
            writes.append((backup, previous))
        updated = text + ("\n" if text and not text.endswith("\n") else "") + block
        writes.append((profile, updated.encode()))
        profiles.append(str(profile))

    # Do not follow user-config symlinks through a parent directory either.
    for path, _ in writes:
        for parent in (path, *path.parents):
            if parent == user_root:
                break
            if parent.is_symlink():
                raise conflict(parent)
    for parent in binary.parents:
        if parent == user_root:
            break
        if parent.is_symlink():
            raise conflict(parent)

    undo = []
    linked = False
    try:
        for path, content in writes:
            previous = path.read_bytes() if path.exists() else None
            if previous == content:
                continue
            atomic_write(path, content)
            undo.append((path, previous))
        if not binary.is_symlink():
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.symlink_to(executable)
            linked = True
    except OSError:
        if linked:
            binary.unlink(missing_ok=True)
        for path, previous in reversed(undo):
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, previous)
        raise

    return {"installed": True, "version": __version__, "executable": str(executable), "cli": str(binary),
            "skills": [str(directory / "SKILL.md") for directory in destinations],
            "profiles_updated": profiles, "new_shell_required_for_path": True,
            "hardware_settings_changed": False,
            "next_commands": [[str(binary), "schema"], [str(binary), "doctor"]]}
