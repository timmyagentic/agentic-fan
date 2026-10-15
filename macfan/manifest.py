"""Machine-readable CLI contract, available without hardware or privileges."""

MANIFEST = {
    "name": "macfan", "version": "0.1.0", "schema_version": 1,
    "description": "Local Mac temperature monitoring and bounded manual fan control.",
    "network_required": False, "model_required": False,
    "desktop_entry": {"option": "--gui", "default_when_bundled_without_arguments": True,
                      "cli_path": "/Applications/MacFan.app/Contents/MacOS/macfan"},
    "global_options": {"--demo": {"type": "boolean", "default": False, "description": "Explicit synthetic backend; never hardware."}},
    "commands": [
        {"command": "schema", "effect": "read", "stream": False, "hardware_required": False},
        {"command": "capabilities", "effect": "read", "stream": False},
        {"command": "sensors", "effect": "read", "stream": False,
         "options": {"--source": {"enum": ["all", "smc", "iohid"], "default": "all"}}},
        {"command": "fans list", "effect": "read", "stream": False},
        {"command": "snapshot", "effect": "read", "stream": False},
        {"command": "watch", "effect": "read", "stream": True,
         "options": {"--interval": {"type": "number", "minimum": 0.2, "maximum": 3600, "default": 1},
                     "--count": {"type": "integer", "minimum": 1, "default": None}}},
        {"command": "fans set", "effect": "hardware_write", "requires_root": True, "stream": True,
         "foreground": True, "restores_auto": True,
         "options": {"--fan": {"type": "string", "repeatable": True, "required": True, "description": "Zero-based fan ID, or all."},
                     "--rpm": {"type": "integer", "required": True, "description": "Positive RPM within every selected fan's reported range."},
                     "--duration": {"type": "number", "minimum": 1, "maximum": 3600, "required": True, "unit": "seconds"},
                     "--experimental-ftst": {"type": "boolean", "default": False, "description": "Opt in to unverified global Ftst control on supporting hardware."}}},
        {"command": "fans auto", "effect": "hardware_write", "requires_root": True, "stream": False,
         "options": {"--fan": {"type": "string", "repeatable": True, "required": True, "description": "Zero-based fan ID, or all."},
                     "--experimental-ftst": {"type": "boolean", "default": False, "description": "Clear Ftst only when all fans have returned to auto."}}},
    ],
    "exit_codes": {"0": "success", "2": "invalid arguments", "3": "unsupported hardware/platform", "4": "permission denied",
                   "5": "hardware, I/O, or internal error", "6": "control busy/conflict", "7": "automatic restoration failed", "130": "interrupted"},
    "output_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["schema_version", "ok", "simulated", "timestamp"],
        "properties": {
            "schema_version": {"const": 1}, "ok": {"type": "boolean"}, "simulated": {"type": "boolean"},
            "timestamp": {"type": "string", "format": "date-time"},
            "event": {"enum": ["sample", "control_started", "control_confirmed", "control_finished"]},
            "data": {"type": "object"},
            "error": {"type": "object", "required": ["code", "message", "details"],
                      "properties": {"code": {"type": "string"}, "message": {"type": "string"}, "details": {"type": "object"}}}
        },
        "oneOf": [{"properties": {"ok": {"const": True}}, "required": ["data"]},
                  {"properties": {"ok": {"const": False}}, "required": ["error"]}]
    },
    "semantics": {
        "temperatures": "Celsius, raw measurements; no imputation or random offsets. SMC labels are unverified keys, IOHID IDs last for this boot.",
        "sensor_status": {"ok": "Finite read succeeded; not a guarantee of sensor calibration or physical mapping.",
                          "unverified": "Zero reading, which may indicate an inactive sensor.", "unavailable": "Read failed; value is null."},
        "control_available": "Recognized readable keys and RPM bounds, not verified physical actuation.",
        "control_confirmed": "Every selected fan was observed within max(100 RPM, 8 percent) of target.",
        "stream_format": "NDJSON: each stdout line is an independent envelope. Read through the final event and check process exit status.",
        "cancel": "Ctrl-C/SIGTERM, closing the consumer, or loss of the CLI triggers guardian restoration. fans auto can interrupt a live session.",
        "limitations": "No guarantee if the guardian is killed or blocked, the OS/kernel fails, or another controller races. No SMART disks, fan curves, or bundled AI model."
    }
}
