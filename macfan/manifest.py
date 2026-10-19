"""Machine-readable CLI contract, available without hardware or privileges."""

from . import SCHEMA_VERSION, __version__
from .temperatures import POLICY, STATUSES
from .rules import DEFAULT

MANIFEST = {
    "name": "macfan", "version": __version__, "schema_version": SCHEMA_VERSION,
    "temperature_validation_policy": dict(POLICY),
    "description": "Local Mac temperature monitoring, bounded fan control and editable temperature rules.",
    "network_required": False, "model_required": False,
    "desktop_entry": {"option": "--gui", "default_when_bundled_without_arguments": True,
                      "cli_path": "/Applications/MacFan.app/Contents/MacOS/macfan"},
    "global_options": {"--demo": {"type": "boolean", "default": False, "description": "Explicit synthetic backend; never hardware."}},
    "commands": [
        {"command": "helper install", "effect": "system_configuration_write", "stream": False,
         "description": "Authorize once to install/update a restricted root-owned local service for this user. Idempotent when current."},
        {"command": "helper status", "effect": "read", "stream": False, "hardware_required": False},
        {"command": "helper uninstall", "effect": "system_configuration_write", "stream": False,
         "description": "Administrator-authorized removal; refuses while a control session is active."},
        {"command": "schema", "effect": "read", "stream": False, "hardware_required": False},
        {"command": "capabilities", "effect": "read", "stream": False},
        {"command": "doctor", "effect": "read", "stream": False,
         "description": "Always produces a diagnostic report if it can run; inspect data.status and monitoring_available, not just ok."},
        {"command": "agent install", "effect": "user_configuration_write", "stream": False, "hardware_required": False,
         "options": {"--target": {"enum": ["codex", "claude", "both"], "default": "codex"},
                     "--user-root": {"type": "string", "description": "User home containing .local/bin and Agent skill directories."}}},
        {"command": "sensors", "effect": "read", "stream": False,
         "options": {"--source": {"enum": ["all", "smc", "iohid"], "default": "all"},
                     "--group": {"enum": ["all", "cpu", "gpu", "memory", "storage", "wireless", "power", "battery", "neural", "system", "unknown"], "default": "all"}}},
        {"command": "fans list", "effect": "read", "stream": False},
        {"command": "snapshot", "effect": "read", "stream": False},
        {"command": "watch", "effect": "read", "stream": True,
         "options": {"--interval": {"type": "number", "minimum": 0.2, "maximum": 3600, "default": 1},
                     "--count": {"type": "integer", "minimum": 1, "default": None}}},
        {"command": "rules show", "effect": "read", "stream": False, "hardware_required": False},
        {"command": "rules validate", "effect": "read", "stream": False, "hardware_required": False},
        {"command": "rules apply", "effect": "user_configuration_write", "stream": False,
         "description": "Validate and atomically save JSON from --file or --stdin. --if-revision prevents stale edits; --wait reports runtime loading separately."},
        {"command": "rules enable", "effect": "user_configuration_write", "stream": False,
         "description": "Enable existing rules; a running native app or rules runner may then control the selected fan."},
        {"command": "rules disable", "effect": "user_configuration_write", "stream": False,
         "description": "Disable rules; --wait waits for a live runner to stop its owned control."},
        {"command": "rules status", "effect": "read", "stream": False, "hardware_required": False},
        {"command": "rules run", "effect": "hardware_write_when_enabled", "stream": True, "foreground": True,
         "requires_helper": True, "restores_auto": True,
         "options": {"--parent-watch": {"type": "boolean", "default": False, "description": "Stop on stdin EOF."},
                     "--count": {"type": "integer", "minimum": 1, "default": None}}},
        {"command": "fans set", "effect": "hardware_write", "requires_root": False, "requires_helper": True, "stream": True,
         "foreground": True, "restores_auto": True,
         "options": {"--fan": {"type": "string", "repeatable": True, "required": True, "description": "Zero-based fan ID, or all."},
                     "--rpm": {"type": "integer", "required": True, "description": "Positive RPM within every selected fan's reported range."},
                     "--duration": {"type": "number", "minimum": 1, "maximum": 3600, "required": True, "unit": "seconds"},
                     "--experimental-ftst": {"type": "boolean", "default": False, "description": "Opt in to unverified global Ftst control on supporting hardware."}}},
        {"command": "fans auto", "effect": "hardware_write", "requires_root": False, "requires_helper": True, "stream": False,
         "options": {"--fan": {"type": "string", "repeatable": True, "required": True, "description": "Zero-based fan ID, or all."},
                     "--experimental-ftst": {"type": "boolean", "default": False, "description": "Clear Ftst only when all fans have returned to auto."}}},
    ],
    "exit_codes": {"0": "success", "2": "invalid arguments", "3": "unsupported hardware/platform", "4": "permission denied",
                   "5": "hardware, I/O, or internal error", "6": "control busy/conflict", "7": "automatic restoration failed", "130": "interrupted"},
    "output_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "required": ["schema_version", "ok", "simulated", "timestamp"],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION}, "ok": {"type": "boolean"}, "simulated": {"type": "boolean"},
            "timestamp": {"type": "string", "format": "date-time"},
            "event": {"enum": ["sample", "control_started", "control_confirmed", "control_finished", "rules_finished"]},
            "data": {"type": "object", "properties": {
                "sensors": {"type": "array", "items": {"$ref": "#/$defs/temperature_sensor"}},
                "sensor_issues": {"type": "array", "items": {"$ref": "#/$defs/temperature_sensor"}},
            }},
            "error": {"type": "object", "required": ["code", "message", "details"],
                      "properties": {"code": {"type": "string"}, "message": {"type": "string"}, "details": {"type": "object"}}}
        },
        "$defs": {"temperature_sensor": {
            "type": "object", "required": ["id", "source", "unit", "value", "raw_value", "status", "reason", "sampled_at", "label_verified"],
            "properties": {
                "id": {"type": "string"}, "source": {"enum": ["smc", "iohid"]}, "unit": {"const": "celsius"},
                "status": {"enum": list(STATUSES)}, "reason": {"type": ["string", "null"]},
                "value": {"type": ["number", "null"]}, "raw_value": {"type": ["number", "null"]},
                "raw_non_finite": {"enum": ["nan", "+infinity", "-infinity"]},
                "sampled_at": {"type": "string", "format": "date-time"}, "label_verified": {"type": "boolean"},
            },
            "oneOf": [
                {"properties": {"status": {"const": "ok"}, "value": {"type": "number"}, "reason": {"type": "null"}}},
                {"properties": {"status": {"enum": ["invalid", "unverified", "unavailable"]}, "value": {"type": "null"}, "reason": {"type": "string"}}},
            ],
        }},
        "oneOf": [{"properties": {"ok": {"const": True}}, "required": ["data"]},
                  {"properties": {"ok": {"const": False}}, "required": ["error"]}]
    },
    "semantics": {
        "temperature_rules": "One local JSON configuration shared by CLI and native app. Rules start disabled. Only identified ok points included in temperature_overview can trigger; calibration/reference rows are excluded. Multiple rules target one selected fan and choose the highest active RPM. Restore after continuously observed release_seconds below release_celsius; missing/stale data releases owned control. A five-second guardian heartbeat lease expires if the runner stalls. External auto requests pause rules until explicit config reapply. The runner lifetime is the native app (closing its window keeps it running) or a foreground rules run command; saving alone never starts a runner.",
        "rules_receipts": "saved means persisted, loaded means a live runner observed this exact file generation, active/cooling means a rule requests control, and control_confirmed means observed RPM reached tolerance. rules status is stale after four seconds without a heartbeat. rules_finished automatic_mode_verified only covers outstanding writes owned by this runner, not other controllers.",
        "sensor_labels": "Additive display_name/group/group_name/label_status/label_source/label_note fields. Original id and name stay unchanged. community_mapping is chip-scoped; system_name translates IOHID Product. label_verified remains false; neither source proves physical calibration or a physical core number.",
        "temperature_overview": "Per identified group, the maximum of currently valid mapped points, with contributing IDs and value_sensor_id. Unknown and calibration rows are excluded. This is not a package sensor or an average. Missing valid data returns null.",
        "authorization": "Run helper install once as the normal user. Subsequent set/auto commands use the local helper without sudo or prompts. Updating/removing the helper may require authorization again. Access is granted to processes of that user, not authenticated by app identity.",
        "readback": "Mode, target and restoration readback are polled for up to 3 seconds per step. Setup counts toward the session duration. No automatic Ftst opt-in or repeated SMC writes.",
        "temperatures": "Celsius; value is populated only for status=ok. Finite interface readings are preserved in raw_value; non-finite values use raw_non_finite. No imputation. Labels/physical locations are unverified; IOHID IDs last for this boot.",
        "sensor_status": {"ok": "Nonzero finite reading within the declared plausibility bounds, not proof of calibration, physical mapping or thermal safety.",
                          "invalid": "Reading is non-finite or outside plausibility bounds. value is null; inspect reason and raw evidence.",
                          "unverified": "Zero reading; value is null and raw_value is zero. May indicate an inactive sensor.",
                          "unavailable": "Read failed or no event; value and raw_value are null."},
        "sensor_summary": "Counts from the current sample: total, readable (including invalid/unverified), ok, invalid, unverified, unavailable, by_source and validation_policy. data_quality is complete, partial, unusable (readable but none ok), or unavailable.",
        "doctor": "temperature_interface_available means at least one interface returned a reading; monitoring_available requires at least one ok sensor. valid_sensor_count counts only ok sensors, not calibrated or independently verified physical sensors. status is limited when monitoring exists but warnings, errors or rejected readings remain. sensor_issues preserves rejected evidence. hardware_write_tested stays false.",
        "schema_migration": "v2 adds invalid status, raw_value, reason and quality summaries. Zero/unverified value is now null. Only consume value for status=ok; raw_value is diagnostic evidence, not fallback temperature.",
        "control_available": "Recognized readable keys and RPM bounds, not verified physical actuation.",
        "control_confirmed": "Every selected fan was observed within max(100 RPM, 8 percent) of target.",
        "stream_format": "NDJSON: each stdout line is an independent envelope. Read through the final event and check process exit status.",
        "cancel": "Ctrl-C/SIGTERM, closing the consumer, or loss of the CLI triggers guardian restoration. fans auto can interrupt a live session.",
        "limitations": "No guarantee if the guardian is killed or blocked, the OS/kernel fails, or another controller races. No SMART disks, interpolated fan curves, or bundled AI model."
    }
}

MANIFEST["rules_configuration"] = {
    "path": "~/Library/Application Support/MacFan/rules.json", "environment_override": "MACFAN_RULES_PATH",
    "default": DEFAULT,
    "schema": {
        "type": "object", "additionalProperties": False, "required": ["version", "enabled", "fan", "rules"],
        "properties": {
            "version": {"const": 1}, "enabled": {"type": "boolean"},
            "fan": {"type": "integer", "minimum": 0, "maximum": 9},
            "rules": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "enabled", "sensor_group", "trigger_celsius", "release_celsius", "release_seconds", "target_rpm"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"},
                    "enabled": {"type": "boolean"},
                    "sensor_group": {"enum": ["cpu", "gpu", "memory", "storage", "wireless", "power", "battery", "neural", "system"]},
                    "trigger_celsius": {"type": "number", "minimum": -40, "maximum": 150},
                    "release_celsius": {"type": "number", "minimum": -40, "maximum": 150},
                    "release_seconds": {"type": "number", "minimum": 0, "maximum": 300},
                    "target_rpm": {"oneOf": [{"const": "max"}, {"type": "integer", "minimum": 1, "maximum": 16383}]},
                },
            }},
        },
    },
}
