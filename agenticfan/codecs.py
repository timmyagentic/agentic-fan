"""SMC values are typed individually; never infer an entire machine's encoding."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .errors import AgenticFanError, invalid


@dataclass(frozen=True)
class KeyInfo:
    kind: str
    size: int
    attributes: int = 0


SIZES = {"sp78": 2, "fpe2": 2, "flt ": 4, "ui8 ": 1, "ui16": 2, "ui32": 4}


def key_number(key: str) -> int:
    try:
        raw = key.encode("ascii")
    except UnicodeEncodeError:
        raise invalid("SMC keys must contain exactly four ASCII bytes.") from None
    if len(raw) != 4:
        raise invalid("SMC keys must contain exactly four ASCII bytes.", key=key)
    return int.from_bytes(raw, "big")


def decode(info: KeyInfo, raw: bytes, *, require_finite=True) -> float:
    expected = SIZES.get(info.kind)
    if expected is None or info.size != expected or len(raw) != expected:
        raise AgenticFanError("UNSUPPORTED_ENCODING", "Unsupported SMC type or payload length.",
                          kind=info.kind, size=info.size, received=len(raw))
    if info.kind == "sp78":
        value = int.from_bytes(raw, "big", signed=True) / 256.0
    elif info.kind == "fpe2":
        value = int.from_bytes(raw, "big") / 4.0
    elif info.kind == "flt ":
        value = float(struct.unpack("<f", raw)[0])
    else:
        value = float(int.from_bytes(raw, "big"))
    if require_finite and not math.isfinite(value):
        raise AgenticFanError("INVALID_READING", "SMC returned a non-finite value.", kind=info.kind)
    return value


def encode_rpm(info: KeyInfo, rpm: float) -> bytes:
    if not math.isfinite(rpm) or rpm < 0:
        raise invalid("RPM must be finite and nonnegative.")
    if info.kind == "fpe2" and info.size == 2:
        if rpm > 16383.75:
            raise invalid("RPM cannot be represented by fpe2.")
        return int(round(rpm * 4)).to_bytes(2, "big")
    if info.kind == "flt " and info.size == 4:
        return struct.pack("<f", rpm)
    raise AgenticFanError("UNSUPPORTED_ENCODING", "The target key has an unsupported RPM encoding.",
                      kind=info.kind, size=info.size)
