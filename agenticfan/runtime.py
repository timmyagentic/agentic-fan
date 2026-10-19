from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .errors import AgenticFanError


def runtime_directory(demo=False) -> Path:
    path = Path(os.environ.get("AGENTICFAN_DEMO_DIR", str(Path(tempfile.gettempdir()) / f"agenticfan-demo-{os.geteuid()}"))) if demo else Path("/var/run/agenticfan")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except PermissionError:
        raise AgenticFanError("PERMISSION_DENIED", "Fan control requires sudo.", 4) from None
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise AgenticFanError("UNSAFE_RUNTIME_DIRECTORY", "Runtime directory must be private and owned by the current effective user.", path=str(path))
    return path


def private_open(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        os.close(fd)
        raise AgenticFanError("UNSAFE_RUNTIME_FILE", "Runtime files must be private regular files with one link.", path=str(path))
    return fd


@contextmanager
def lock(directory: Path, name: str, blocking=True):
    fd = private_open(directory / name)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise AgenticFanError("CONTROL_BUSY", "A agenticfan control session is already active.", 6) from None
        yield
    finally:
        os.close(fd)
