from __future__ import annotations

import json

from .hardware import now


def success(data, simulated=False, event=None):
    result = {"schema_version": 1, "ok": True, "simulated": simulated, "timestamp": now(), "data": data}
    if event:
        result["event"] = event
    return result


def failure(error, simulated=False):
    return {"schema_version": 1, "ok": False, "simulated": simulated, "timestamp": now(), "error": error.as_dict()}


def emit(message):
    print(json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
