"""Machine-readable CLI contract, available without hardware or privileges."""

from . import SCHEMA_VERSION, __version__
from .temperatures import POLICY, STATUSES
from .rules import DEFAULT
from .hook_files import TEMPLATE as HOOK_TEMPLATE, MAX_BYTES as HOOK_MAX_BYTES, EVENT_EXAMPLE

MANIFEST = {
    "name": "agenticfan", "version": __version__, "schema_version": SCHEMA_VERSION,
    "temperature_validation_policy": dict(POLICY),
    "description": "Local Mac temperature monitoring, bounded fan control and editable temperature rules.",
    "network_required": False, "model_required": False,
    "desktop_entry": {"option": "--gui", "default_when_bundled_without_arguments": True,
                      "cli_path": "/Applications/AgenticFan.app/Contents/MacOS/agenticfan"},
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
            "event": {"enum": ["sample", "control_started", "control_target_updated", "control_feedback", "control_confirmed", "control_finished", "rules_finished"]},
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
        "temperature_rules": "One local JSON configuration shared by CLI and native app. Rules start disabled. Optional trigger_seconds requires continuously observed high readings (default 0); optional hook receives one asynchronous user-process rule_triggered JSON event per activation. Hooks never run as root, do not retry, and cannot pause fan control. Only identified ok points included in temperature_overview can trigger; calibration/reference rows are excluded. Multiple rules target one selected fan and choose the highest active RPM. Restore after continuously observed release_seconds below release_celsius; missing/stale data releases owned control. A five-second guardian heartbeat lease expires if the runner stalls. External auto requests pause rules until explicit config reapply. The runner lifetime is the native app (closing its window keeps it running) or a foreground rules run command; saving alone never starts a runner.",
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
        "control_confirmed": "The event records an observation within max(100 RPM, 8 percent) of that target/sequence. The rules runtime boolean requires a fresh observation of the exact latest requested sequence; it may be false during normal retargeting. It is not the continuous tracking health indicator.",
        "control_feedback": "Leased guardians emit one control_feedback per hardware sample BEFORE applying the next target. The event contains sequence, target_rpm, actual_rpm by fan ID, tolerance_rpm, within_tolerance, sampled_at (UTC), and sampled_monotonic (same-host boot clock). Runtime control_feedback selects the owned fan's scalar actual_rpm and adds status (waiting/tracking/adjusting/stale/stopped), age_seconds and pending_update. tracking means the observed RPM follows the applied target even if a newer request is pending; it never confirms that newer sequence. Feedback older than 3 seconds or from the future cannot establish tracking. Keep only the latest observation from this session; stopped/failed sessions cannot transfer health to another session. Consumers must expire displayed snapshots too. Continuous mismatch still ends control and restores automatic mode after 15 seconds, even after earlier confirmation or with moving targets.",
        "stream_format": "NDJSON: each stdout line is an independent envelope. Read through the final event and check process exit status.",
        "cancel": "Ctrl-C/SIGTERM, closing the consumer, or loss of the CLI triggers guardian restoration. fans auto can interrupt a live session.",
        "limitations": "No guarantee if the guardian is killed or blocked, the OS/kernel fails, or another controller races. No SMART disks or bundled AI model."
    }
}

MANIFEST["rules_configuration"] = {
    "path": "~/Library/Application Support/AgenticFan/rules.json", "environment_override": "AGENTICFAN_RULES_PATH",
    "default": DEFAULT,
    "schema": {
        "type": "object", "additionalProperties": False, "required": ["version", "enabled", "fan", "rules"],
        "properties": {
            "version": {"const": 1}, "enabled": {"type": "boolean"},
            "mode": {"enum": ["rules", "curve"], "default": "rules", "description": "Absent preserves legacy threshold rules. enabled=false selects system automatic mode."},
            "curve": {"type": "object", "additionalProperties": False, "required": ["sensor_group", "nodes"],
                      "properties": {
                          "sensor_group": {"enum": ["cpu", "gpu", "memory", "storage", "wireless", "power", "battery", "neural", "system"]},
                          "ramp_rpm_per_second": {"type": "number", "minimum": 1, "maximum": 1000, "default": 200},
                          "nodes": {"type": "array", "minItems": 2, "maxItems": 16, "items": {
                              "type": "object", "additionalProperties": False, "required": ["temperature_celsius", "target_rpm"],
                              "properties": {"temperature_celsius": {"type": "number", "minimum": -40, "maximum": 150},
                                             "target_rpm": {"type": "integer", "minimum": 1, "maximum": 16383}}}},
                      }, "description": "Strictly increasing temperatures, nondecreasing RPM, all nodes within the selected fan's hardware bounds. Linear interpolation, endpoint clamping, then a time-based slew limit seeded from observed RPM."},
            "fan": {"type": "integer", "minimum": 0, "maximum": 9},
            "hook": {"description": "Optional user executable receiving one rule_triggered JSON event on stdin. No shell; no root execution; no automatic retries.",
                     "oneOf": [{"type": "null"}, {"type": "object", "additionalProperties": False,
                       "required": ["program"], "properties": {
                           "program": {"type": "string", "pattern": "^/", "description": "Absolute executable path. Runs as the current user, with its parent directory as cwd."},
                           "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 60, "default": 10},
                       }}]},
            "rules": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "enabled", "sensor_group", "trigger_celsius", "release_celsius", "release_seconds", "target_rpm"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"},
                    "enabled": {"type": "boolean"},
                    "sensor_group": {"enum": ["cpu", "gpu", "memory", "storage", "wireless", "power", "battery", "neural", "system"]},
                    "trigger_celsius": {"type": "number", "minimum": -40, "maximum": 150},
                    "trigger_seconds": {"type": "number", "minimum": 0, "maximum": 300, "default": 0},
                    "release_celsius": {"type": "number", "minimum": -40, "maximum": 150},
                    "release_seconds": {"type": "number", "minimum": 0, "maximum": 300},
                    "target_rpm": {"oneOf": [{"const": "max"}, {"type": "integer", "minimum": 1, "maximum": 16383}]},
                },
            }},
        },
        "allOf": [{"if": {"required": ["mode"], "properties": {"mode": {"const": "curve"}}}, "then": {"required": ["curve"]}}],
    },
}
MANIFEST['semantics']['temperature_curves'] = (
    'Optional mode=curve uses one identified temperature group and 2..16 editable temperature/RPM nodes. '
    'Interpolate linearly, hold endpoint RPM outside the node range, and apply ramp_rpm_per_second (default 200) '
    'in both directions, starting at current observed fan speed. curve_target_rpm is the interpolated goal; '
    'desired_rpm is the smoothed request. One bounded leased guardian session accepts sequenced RPM-only updates '
    'without toggling automatic mode between targets. Updates do not extend its 3600-second deadline or heartbeat lease. '
    'Confirmation is tied to the exact update sequence; moving targets cannot reset the 15-second tracking failure timer. '
    'Missing/stale temperature, invalid config, stop or expiry releases owned control. External auto pauses until explicit reapply. '
    'Curve mode retains threshold rules and Hook settings but does not evaluate rules or submit threshold Hook events. '
    'No configured mode defaults to legacy rules; enabled=false disables either mode. No curve is enabled implicitly.'
)

_hook_selection = {
    '--config': {'type': 'string', 'description': 'Rules JSON used to select the current Hook; same override/default as rules commands.'},
    '--default': {'type': 'boolean', 'default': False, 'description': 'Select hooks/threshold-hook.sh beside the rules file; selecting alone does not replace the configured Hook.'},
}
_hook_input = {
    '--stdin': {'type': 'boolean', 'description': 'Read raw UTF-8 script content, not JSON. Mutually exclusive with --file.'},
    '--file': {'type': 'string', 'description': 'Input script content. This is not the script destination.'},
}
MANIFEST['commands'] += [
    {'command': 'hook show', 'effect': 'read', 'stream': False, 'hardware_required': False,
     'description': 'Discover script path/content, default template, independent script/rules revisions and execution evidence without creating files.',
     'options': _hook_selection},
    {'command': 'hook apply', 'effect': 'user_configuration_write', 'stream': False, 'hardware_required': False,
     'description': 'Validate and atomically save the script with executable permissions. Preserve Hook enablement unless explicitly requested; never change global rules enablement or run the hook directly.',
     'options': {**_hook_selection, **_hook_input,
                 '--if-revision': {'type': 'string', 'description': 'Required for script writes or enabling: script.revision from hook show, including missing for first creation.'},
                 '--if-rules-revision': {'type': 'string', 'required': True, 'description': 'settings.rules_revision from hook show; protects file selection and Hook settings.'},
                 '--enable': {'type': 'boolean', 'description': 'Explicitly configure the selected script. Mutually exclusive with --disable.'},
                 '--disable': {'type': 'boolean', 'description': 'Remove the Hook setting, retaining the script file and global rules state.'},
                 '--timeout-seconds': {'type': 'number', 'minimum': 0.1, 'maximum': 60},
                 '--wait': {'type': 'number', 'minimum': 0, 'maximum': 10, 'default': 0, 'description': 'Wait for rules settings loading, not for script execution.'}}},
    {'command': 'hook validate', 'effect': 'read', 'stream': False, 'hardware_required': False,
     'description': 'Check the saved file, or normalize/check draft content from stdin/file. Invalid checks return HOOK_VALIDATION_FAILED and exit 2. Never execute the Hook or its custom interpreter; unsupported syntax is reported explicitly.',
     'options': {**_hook_selection, **_hook_input}},
    {'command': 'hook status', 'effect': 'read', 'stream': False, 'hardware_required': False,
     'description': 'Read script metadata (without content), validation, runner state and the last execution observations; does not initialize files.',
     'options': _hook_selection},
]
MANIFEST['hook_files'] = {
    'default_relative_path': 'hooks/threshold-hook.sh', 'default_content': HOOK_TEMPLATE,
    'maximum_bytes': HOOK_MAX_BYTES, 'input_format': 'raw UTF-8 script; JSON is only the response envelope',
    'revision': {'algorithm': 'sha256', 'scope': 'script file bytes', 'missing': 'missing',
                 'unavailable': None, 'independent_rules_revision': 'settings.rules_revision'},
    'show_fields': {'script': ['path', 'default_path', 'exists', 'editable', 'executable', 'revision', 'content', 'issue'],
                    'settings': ['enabled', 'timeout_seconds', 'rules_enabled', 'rules_revision', 'configured_program', 'selected_program_is_configured'],
                    'execution': ['runner_running', 'runner_status', 'settings_loaded', 'running', 'queued', 'dropped', 'last_result', 'last_observed_revision_matches_current']},
    'validation_fields': ['valid', 'scope', 'path', 'revision', 'interpreter', 'syntax', 'issues', 'warnings', 'executed'],
    'syntax_statuses': ['passed', 'failed', 'unsupported', 'unavailable'],
    'apply_fields': ['saved', 'script_saved', 'script_saved_revision', 'settings_saved', 'rules_revision', 'rules_generation', 'rules_loaded', 'script_effect', 'validation', 'document'],
    'errors': {'HOOK_CONFLICT': 'Script bytes changed; reload and merge.', 'CONFIG_CONFLICT': 'Rules settings or script selection changed; reload and merge.',
               'HOOK_BUSY': 'Another cooperating writer is active; reload and retry.',
               'HOOK_VALIDATION_FAILED': 'Inspect error.details.validation; no file was written by validation.',
               'HOOK_APPLY_PARTIAL': 'Inspect script_saved/settings_saved and reload; two files are not one filesystem transaction.'},
    'event_example': EVENT_EXAMPLE,
    'semantics': [
        'Only apply writes files. show/validate/status are read-only, including default discovery in demo mode.',
        'New or shebang-free content receives #!/bin/sh. Default content performs no action. Reads do not create the default file.',
        'Apply sets owner execute permission, checks both revisions, and preserves other temperature rules. Script-only apply does not rewrite the rules file.',
        'Existing UTF-8 regular scripts are edited in place. Binary, linked and oversized files are not rewritten by the editor; --default offers an explicit alternative.',
        'Syntax checking uses fixed system sh/bash in no-execution mode or the bundled Python AST parser. Other interpreters are never launched for validation and report unsupported syntax.',
        'Interpreter resolution may depend on the calling process PATH. Validation does not prove external dependencies, business results or another process environment.',
        'Execution receipts include program, script_revision, script_revision_after, timestamps and duration. These are byte observations around execution, not immutable snapshots; external editors can race.',
        'last_observed_revision_matches_current is null without comparable evidence. True does not imply successful business actions; inspect last_result.status separately.',
        'Changing an enabled script affects the next event processed by its runner; apply and validate never invoke it as a test. No shell command-string or privileged Helper execution is introduced.',
    ],
}
