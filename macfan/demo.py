"""Explicit, persistent simulation. Never selected as a fallback for native errors."""
from __future__ import annotations

import json
import os
import struct

from .codecs import KeyInfo, decode
from .errors import MissingKey
from .hardware import Hardware
from .runtime import lock, private_open, runtime_directory


def fixture() -> dict[str, tuple[KeyInfo, bytes]]:
    values = {"FNum": (KeyInfo("ui8 ", 1), b"\2"), "Ftst": (KeyInfo("ui8 ", 1), b"\0")}
    for index in range(2):
        for suffix, value in (("Ac", 1800), ("Mn", 1200), ("Mx", 6200), ("Tg", 1800)):
            values[f"F{index}{suffix}"] = (KeyInfo("flt ", 4), struct.pack("<f", value))
        values[f"F{index}Md"] = (KeyInfo("ui8 ", 1), b"\0")
    for key, value in (("TCDX", 47.5), ("Tg0D", 42), ("TB0T", 31), ("Tp00", 0)):
        values[key] = (KeyInfo("flt ", 4), struct.pack("<f", value))
    values["#KEY"] = (KeyInfo("ui32", 4), (len(values) + 1).to_bytes(4, "big"))
    return values


class MemorySMC:
    def __init__(self, values=None):
        self.values = dict(fixture() if values is None else values)
        self.writes = []

    def info(self, key):
        if key not in self.values:
            raise MissingKey(key)
        return self.values[key][0]

    def read(self, key):
        self.info(key)
        return self.values[key][1]

    def write(self, key, raw):
        info = self.info(key)
        self.writes.append((key, raw))
        self.values[key] = (info, raw)
        if key.startswith("F") and key.endswith("Tg"):
            self.values[key[:2] + "Ac"] = (info, raw)
        if key.endswith(("Md", "md")) and raw == b"\0":
            self.values[key[:2] + "Ac"] = (KeyInfo("flt ", 4), struct.pack("<f", 1800))

    def keys(self):
        return sorted(self.values)

    def close(self):
        pass


class FileSMC(MemorySMC):
    def __init__(self):
        super().__init__()
        self.directory = runtime_directory(demo=True)
        with lock(self.directory, "state.lock"):
            self._load()
            self._save()

    def _load(self):
        fd = private_open(self.directory / "state.json")
        with os.fdopen(fd, "r") as handle:
            text = handle.read(65537)
        if len(text) > 65536:
            raise ValueError("Demo state exceeds 64 KiB.")
        if text:
            state = json.loads(text)
            for key, raw in state.items():
                if key in self.values:
                    self.values[key] = (self.values[key][0], bytes.fromhex(raw))

    def _save(self):
        fd = private_open(self.directory / "state.json")
        os.ftruncate(fd, 0)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps({key: raw.hex() for key, (_, raw) in self.values.items()}))
            handle.flush()
            os.fsync(handle.fileno())

    def read(self, key):
        with lock(self.directory, "state.lock"):
            self._load()
            return super().read(key)

    def write(self, key, raw):
        with lock(self.directory, "state.lock"):
            self._load()
            super().write(key, raw)
            self._save()


def demo_hardware() -> Hardware:
    return Hardware(FileSMC(), simulated=True, warnings=[{
        "code": "SIMULATED_DATA", "message": "Synthetic sensors and fan state; no hardware is accessed."}])
