from __future__ import annotations

import math
import os
from datetime import datetime, timezone

from .codecs import KeyInfo, decode, encode_rpm
from .errors import MacFanError, MissingKey, invalid
from .temperatures import reading


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Hardware:
    """Typed hardware operations shared by native and deterministic test transports."""

    def __init__(self, transport, hid=None, simulated=False, warnings=None):
        self.smc = transport
        self.hid = hid
        self.simulated = simulated
        self.warnings = list(warnings or [])
        self._temperature_keys = None

    def optional_info(self, key: str):
        try:
            return self.smc.info(key)
        except MissingKey:
            return None

    def number(self, key: str) -> float:
        return decode(self.smc.info(key), self.smc.read(key))

    def count(self) -> int:
        value = self.number("FNum")
        if value != int(value) or not 0 <= value <= 10:
            raise MacFanError("UNSUPPORTED_FAN_COUNT", "Expected 0..10 fans with single-digit SMC indices.", count=value)
        return int(value)

    def mode_key(self, index: int) -> str | None:
        # Do not interpret an I/O failure as a missing key.
        for key in (f"F{index}md", f"F{index}Md"):
            info = self.optional_info(key)
            if info is not None:
                if info.kind != "ui8 " or info.size != 1:
                    raise MacFanError("UNSUPPORTED_ENCODING", "Unsupported fan mode encoding.", key=key)
                return key
        info = self.optional_info("FS! ")
        if info is not None:
            if info.size != 2 or info.kind not in ("ui16", "hex_"):
                raise MacFanError("UNSUPPORTED_ENCODING", "Unsupported fan bitmap encoding.")
            return "FS! "
        return None

    def mode(self, index: int, key: str | None = None) -> str:
        key = key or self.mode_key(index)
        if key is None:
            return "unknown"
        raw = self.smc.read(key)
        value = (int.from_bytes(raw, "big") >> index) & 1 if key == "FS! " else raw[0]
        return {0: "auto", 1: "manual"}.get(value, "unknown")

    def fans(self) -> list[dict]:
        rows = []
        for index in range(self.count()):
            row = {"id": index, "name": f"Fan {index + 1}", "unit": "rpm", "errors": [],
                   "control_available": False, "control_validation": "unverified"}
            try:
                if self.optional_info(f"F{index}ID"):
                    label = self.smc.read(f"F{index}ID")[4:16].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
                    if label:
                        row["name"] = label
            except MacFanError as exc:
                row["errors"].append(exc.as_dict())
            for field, suffix in (("actual_rpm", "Ac"), ("min_rpm", "Mn"), ("max_rpm", "Mx"), ("target_rpm", "Tg")):
                try:
                    row[field] = self.number(f"F{index}{suffix}")
                except MacFanError as exc:
                    row[field] = None
                    row["errors"].append(exc.as_dict())
            try:
                row["mode_key"] = self.mode_key(index)
                row["mode"] = self.mode(index, row["mode_key"])
                info = self.optional_info(f"F{index}Tg")
                row["control_available"] = bool(row["mode_key"] and info and
                    (info.kind, info.size) in (("fpe2", 2), ("flt ", 4)) and
                    all(row[x] is not None for x in ("actual_rpm", "min_rpm", "max_rpm", "target_rpm")) and
                    0 <= row["min_rpm"] < row["max_rpm"])
            except MacFanError as exc:
                row.update(mode="unknown", mode_key=None)
                row["errors"].append(exc.as_dict())
            rows.append(row)
        return rows

    def temperatures(self) -> list[dict]:
        timestamp = now()
        if self._temperature_keys is None:
            keys = []
            for key in self.smc.keys():
                if key.startswith("T"):
                    try:
                        info = self.smc.info(key)
                        if (info.kind, info.size) in (("sp78", 2), ("flt ", 4)):
                            keys.append(key)
                    except MacFanError as exc:
                        self.warnings.append(exc.as_dict())
            self._temperature_keys = keys
        rows = []
        for key in self._temperature_keys:
            row = {"id": f"smc:{key}", "name": key, "key": key, "source": "smc", "unit": "celsius",
                   **reading(), "sampled_at": timestamp,
                   "estimated": False, "identity_scope": "machine", "label_verified": False}
            try:
                # Preserve non-finite evidence for temperature validation only.
                # Fan/RPM decoding continues to reject non-finite values.
                value = decode(self.smc.info(key), self.smc.read(key), require_finite=False)
                row.update(reading(value))
            except MacFanError as exc:
                row["error"] = exc.as_dict()
            rows.append(row)
        if self.hid:
            rows.extend(self.hid.read(timestamp))
        return rows

    def require_write(self):
        if not self.simulated and os.geteuid() != 0:
            raise MacFanError("PERMISSION_DENIED", "Fan writes require an explicitly privileged invocation (sudo). Monitoring does not.", 4)

    def _set_mode(self, index: int, manual: bool):
        key = self.mode_key(index)
        if not key:
            raise MacFanError("CONTROL_UNSUPPORTED", "No recognized fan mode key.", 3, fan=index)
        if key == "FS! ":
            current = int.from_bytes(self.smc.read(key), "big")
            updated = current | (1 << index) if manual else current & ~(1 << index)
            raw = updated.to_bytes(2, "big")
        else:
            raw = bytes([int(manual)])
        self.smc.write(key, raw)

    def validate_control(self, indices: list[int], rpm: int, experimental_ftst=False):
        self.require_write()
        available = {row["id"]: row for row in self.fans()}
        if not indices or len(indices) != len(set(indices)):
            raise invalid("Select at least one distinct fan.")
        for index in indices:
            if index not in available:
                raise invalid("Fan index does not exist.", fan=index)
            fan = available[index]
            for error in fan["errors"]:
                if error["code"] not in ("KEY_NOT_FOUND", "UNSUPPORTED_ENCODING"):
                    raise MacFanError(error["code"], error["message"], **error["details"])
            if not fan["control_available"] or fan["mode"] == "unknown":
                raise MacFanError("CONTROL_UNSUPPORTED", "Fan lacks a recognized, readable control interface.", 3,
                                  fan=index, failures=fan["errors"])
            if fan["mode"] != "auto":
                raise MacFanError("CONTROL_CONFLICT", "A selected fan is already under manual control. Restore auto explicitly first.", 6, fan=index)
            if rpm <= 0 or not fan["min_rpm"] <= rpm <= fan["max_rpm"] or not math.isfinite(rpm):
                raise invalid("RPM must be positive and within this fan's hardware range.",
                              fan=index, minimum=fan["min_rpm"], maximum=fan["max_rpm"])
            encode_rpm(self.smc.info(f"F{index}Tg"), rpm)
        if experimental_ftst:
            info = self.optional_info("Ftst")
            if info is None or (info.kind, info.size) != ("ui8 ", 1):
                raise MacFanError("CONTROL_UNSUPPORTED", "No recognized Ftst interface.", 3)
            if self.smc.read("Ftst") != b"\0" or any(row["mode"] != "auto" for row in available.values()):
                raise MacFanError("CONTROL_CONFLICT", "Experimental global Ftst control requires all fans in auto and Ftst cleared.", 6)
        elif self.optional_info("Ftst") and self.smc.read("Ftst") != b"\0":
            raise MacFanError("CONTROL_CONFLICT", "Global Ftst is already active. Restore the existing controller before starting a session.", 6)

    def apply(self, indices: list[int], rpm: int, experimental_ftst=False):
        # Caller must validate and hold the write lock. Its finally block owns rollback,
        # including a partial failure before this method returns.
        for index in indices:
            self._set_mode(index, True)
        if experimental_ftst:
            self.smc.write("Ftst", b"\1")
        for index in indices:
            key = f"F{index}Tg"
            self.smc.write(key, encode_rpm(self.smc.info(key), rpm))
        for index in indices:
            if self.mode(index) != "manual" or abs(self.number(f"F{index}Tg") - rpm) > 1:
                raise MacFanError("CONTROL_REJECTED", "Hardware did not retain the requested mode/target.", fan=index)

    def restore(self, indices: list[int], experimental_ftst=False):
        self.require_write()
        errors = []
        for index in indices:
            try:
                self._set_mode(index, False)
                if self.mode(index) != "auto":
                    raise MacFanError("RESTORE_REJECTED", "Fan did not return to auto.", fan=index)
            except MacFanError as exc:
                errors.append(exc.as_dict())
        if experimental_ftst:
            try:
                if all(self.mode(i) == "auto" for i in range(self.count())):
                    self.smc.write("Ftst", b"\0")
                    if self.smc.read("Ftst") != b"\0":
                        raise MacFanError("RESTORE_REJECTED", "Ftst did not clear.")
                else:
                    raise MacFanError("RESTORE_CONFLICT", "Another manual fan prevents clearing global Ftst.")
            except MacFanError as exc:
                errors.append(exc.as_dict())
        else:
            try:
                if self.optional_info("Ftst") and self.smc.read("Ftst") != b"\0":
                    raise MacFanError("FTST_STILL_ACTIVE", "Fan modes are auto, but global Ftst is active. Its recovery requires --experimental-ftst.")
            except MacFanError as exc:
                errors.append(exc.as_dict())
        if errors:
            raise MacFanError("RESTORE_FAILED", "Automatic control could not be verified. Retry fans auto and inspect the hardware.", 7, failures=errors)

    def close(self):
        if self.hid:
            self.hid.close()
        self.smc.close()


def native_hardware(include_temperatures=True) -> Hardware:
    from .smc import AppleSMC
    smc = AppleSMC()
    hid = None
    warnings = []
    if include_temperatures:
        from .hid import HIDTemperatures
        try:
            hid = HIDTemperatures()
        except (OSError, AttributeError, MacFanError) as exc:
            warnings.append({"code": "HID_UNAVAILABLE", "message": str(exc)})
    return Hardware(smc, hid=hid, warnings=warnings)
