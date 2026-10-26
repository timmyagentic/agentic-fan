"""User-owned temperature rules, shared by the native app and the JSON CLI.

The runner owns one fan, consumes only identified valid temperature points, and
uses the existing bounded guardian. Losing this process closes its helper socket;
neither a detached CLI nor an unobserved fan-control process is left behind.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import select
import signal
import tempfile
import threading
import time
import uuid

from .errors import AgenticFanError, invalid
from .protocol import failure, success
from .runtime import lock, runtime_directory
from .rule_hooks import HookDispatcher, validate_hook

MAX_BYTES = 65536
GROUPS = {"cpu", "gpu", "memory", "storage", "wireless", "power", "battery", "neural", "system"}
DEFAULT = {"version": 1, "enabled": False, "fan": 0, "rules": [
    {"id": "cpu-hot", "enabled": True, "sensor_group": "cpu", "trigger_celsius": 99,
     "release_celsius": 95, "release_seconds": 5, "target_rpm": "max"},
]}


def default_path(demo=False, create_runtime=True):
    if os.environ.get("AGENTICFAN_RULES_PATH"):
        return Path(os.environ["AGENTICFAN_RULES_PATH"]).expanduser().absolute()
    if demo:
        return runtime_directory(True, create=create_runtime) / "rules.json"
    return Path.home() / "Library/Application Support/AgenticFan/rules.json"


def _number(value):
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def validate(config):
    fields = {"version", "enabled", "fan", "rules"}
    if not isinstance(config, dict) or not fields <= set(config) or set(config) - fields - {"hook"}:
        raise invalid("Rules require version, enabled, fan and rules, with optional hook.")
    validate_hook(config.get("hook"))
    if type(config["version"]) is not int or config["version"] != 1:
        raise invalid("Unsupported rules version; expected 1.")
    if type(config["enabled"]) is not bool or type(config["fan"]) is not int or not 0 <= config["fan"] <= 9:
        raise invalid("enabled must be boolean and fan an integer ID from 0 through 9.")
    if not isinstance(config["rules"], list) or not 1 <= len(config["rules"]) <= 16:
        raise invalid("Provide 1 through 16 rules for the selected fan.")
    ids = set()
    for rule in config["rules"]:
        fields = {"id", "enabled", "sensor_group", "trigger_celsius", "release_celsius", "release_seconds", "target_rpm"}
        if not isinstance(rule, dict) or not fields <= set(rule) or set(rule) - fields - {"trigger_seconds"}:
            raise invalid("Each rule requires id, enabled, sensor_group, trigger_celsius, release_celsius, release_seconds and target_rpm, with optional trigger_seconds.")
        name = rule["id"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name) or name in ids:
            raise invalid("Rule IDs must be unique, 1..64 letters, digits, underscores or hyphens.")
        ids.add(name)
        if (type(rule["enabled"]) is not bool or not isinstance(rule["sensor_group"], str)
                or rule["sensor_group"] not in GROUPS):
            raise invalid("Rule enabled must be boolean and sensor_group an identified component group.")
        high, low, delay = (rule[k] for k in ("trigger_celsius", "release_celsius", "release_seconds"))
        if not all(_number(x) for x in (high, low, delay)) or not -40 <= low < high <= 150 or not 0 <= delay <= 300:
            raise invalid("Use finite release < trigger temperatures within -40..150 C and release_seconds from 0 through 300. These are plausibility bounds, not safe operating limits.")
        trigger_delay = rule.get("trigger_seconds", 0)
        if not _number(trigger_delay) or not 0 <= trigger_delay <= 300:
            raise invalid("trigger_seconds must be finite and between 0 and 300 seconds.")
        rpm = rule["target_rpm"]
        if rpm != "max" and (type(rpm) is not int or not 1 <= rpm <= 16383):
            raise invalid("target_rpm must be 'max' or an integer RPM from 1 through 16383.")
    return deepcopy(config)


def decode(raw):
    if len(raw) > MAX_BYTES:
        raise invalid("Rules JSON exceeds 65536 bytes.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite number: " + value)))
    except (ValueError, UnicodeError) as exc:
        raise invalid("Invalid rules JSON: " + str(exc)) from None
    return validate(value)


@dataclass
class Document:
    path: Path
    config: dict
    revision: str
    generation: str
    exists: bool


def load(path):
    path = Path(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
            stat = os.fstat(stream.fileno())
        config = decode(raw)
        generation = f"{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}"
        exists = True
    except FileNotFoundError:
        config = deepcopy(DEFAULT)
        raw = json.dumps(config, sort_keys=True).encode()
        generation = "missing"
        exists = False
    return Document(path, config, hashlib.sha256(raw).hexdigest(), generation, exists)


def atomic_json(path, value, durable=False):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def save(path, config, expected_revision=None):
    config = validate(config)  # Reject before creating directories or replacing bytes.
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock(path.parent, "." + path.name + ".writer.lock"):
        if expected_revision is not None and load(path).revision != expected_revision:
            raise AgenticFanError("CONFIG_CONFLICT", "Rules changed since they were read. Reload and merge your edits before applying.", 6)
        atomic_json(path, config, durable=True)
        return load(path)


def state_path(path):
    path = Path(path)
    return path.with_name(path.name + ".state.json")


def status(path):
    try:
        with state_path(path).open("rb") as stream:
            data = json.loads(stream.read(MAX_BYTES + 1))
        age = max(0, time.time() - data["updated_at_unix"])
        alive = False
        if data.get("running") is True:
            try:
                os.kill(data["pid"], 0)
                alive = True
            except OSError:
                pass
        running = alive and age <= 4
        return {**data, "running": running, "heartbeat_age_seconds": age,
                "status": data.get("status") if running or not data.get("running") else "stale"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"running": False, "status": "not_running", "config_path": str(path)}


def saved_result(document, wait=0):
    if not _number(wait) or not 0 <= wait <= 10:
        raise invalid("--wait must be finite and between 0 and 10 seconds.")
    deadline = time.monotonic() + wait
    while True:
        runtime = status(document.path)
        loaded = (runtime.get("running") is True and runtime.get("loaded_generation") == document.generation
                  and runtime.get("status") != "restoring")
        if loaded or not runtime.get("running") or time.monotonic() >= deadline:
            break
        time.sleep(.05)
    return {"saved": True, "path": str(document.path), "config": document.config,
            "revision": document.revision, "generation": document.generation,
            "runtime_running": runtime.get("running", False), "loaded": loaded, "runtime": runtime}


class Evaluator:
    def __init__(self, config):
        self.config = validate(config)
        self.active = set()
        self.hot_since = {}
        self.cool_since = {}
        self.last_sample = None

    def reconfigure(self, config):
        updated = validate(config)
        same_owner = (self.config["fan"], self.config["enabled"]) == (updated["fan"], updated["enabled"])
        previous = {rule["id"]: {"trigger_seconds": 0, **rule} for rule in self.config["rules"]}
        unchanged = {rule["id"] for rule in updated["rules"]
                     if same_owner and previous.get(rule["id"]) == {"trigger_seconds": 0, **rule}}
        self.active.intersection_update(unchanged)
        self.hot_since = {key: value for key, value in self.hot_since.items() if key in unchanged}
        self.cool_since = {key: value for key, value in self.cool_since.items() if key in unchanged}
        self.config = updated

    def evaluate(self, sample, now):
        # A gap cannot count as five continuously observed cool seconds.
        if self.last_sample is not None and now - self.last_sample > 3:
            self.hot_since.clear()
            self.cool_since.clear()
        self.last_sample = now
        readings = {}
        for component in sample.get("temperature_overview", []):
            ids = set(component.get("sensor_ids", []))
            values = [sensor["value"] for sensor in sample.get("sensors", [])
                      if sensor.get("id") in ids and sensor.get("status") == "ok"
                      and not sensor.get("exclude_from_overview") and _number(sensor.get("value"))]
            readings[component["group"]] = max(values) if values else None
        fan = next((fan for fan in sample.get("fans", []) if fan["id"] == self.config["fan"]), None)
        enabled = [rule for rule in self.config["rules"] if self.config["enabled"] and rule["enabled"]]
        targets = []
        rule_states = []
        triggered = []
        for rule in enabled:
            name, group = rule["id"], rule["sensor_group"]
            value = readings.get(group)
            if value is None:
                self.active.discard(name)
                self.hot_since.pop(name, None)
                self.cool_since.pop(name, None)
            elif name not in self.active:
                if value >= rule["trigger_celsius"]:
                    self.hot_since.setdefault(name, now)
                    if now - self.hot_since[name] >= rule.get("trigger_seconds", 0):
                        self.active.add(name)
                        triggered.append(name)
                        self.hot_since.pop(name, None)
                else:
                    self.hot_since.pop(name, None)
            if name in self.active:
                if value <= rule["release_celsius"]:
                    self.cool_since.setdefault(name, now)
                    if now - self.cool_since[name] >= rule["release_seconds"]:
                        self.active.discard(name)
                        self.cool_since.pop(name, None)
                else:
                    self.cool_since.pop(name, None)
            active = name in self.active
            if active:
                if (not fan or not fan.get("control_available") or not _number(fan.get("min_rpm"))
                        or not _number(fan.get("max_rpm")) or not 0 < fan["min_rpm"] < fan["max_rpm"]):
                    raise AgenticFanError("RULE_FAN_UNAVAILABLE", "Selected fan has no usable control interface or RPM bounds.")
                rpm = math.floor(fan["max_rpm"]) if rule["target_rpm"] == "max" else rule["target_rpm"]
                if not fan["min_rpm"] <= rpm <= fan["max_rpm"]:
                    raise invalid("Rule target is outside this fan's reported RPM bounds.", rule_id=name,
                                  target_rpm=rpm, min_rpm=fan["min_rpm"], max_rpm=fan["max_rpm"])
                targets.append(rpm)
            rule_states.append({"id": name, "value": value, "active": active,
                                "hot_seconds": max(0, now - self.hot_since[name]) if name in self.hot_since else 0,
                                "pending": name in self.hot_since,
                                "cool_seconds": max(0, now - self.cool_since[name]) if name in self.cool_since else 0})
        return {"desired_rpm": max(targets) if targets else None, "active_rule_ids": sorted(self.active),
                "triggered_rule_ids": triggered, "pending": bool(self.hot_since),
                "cooling": bool(self.cool_since), "readings": readings, "rule_states": rule_states,
                "readings_available": not enabled or any(readings.get(rule["sensor_group"]) is not None for rule in enabled)}


class Control:
    """One observed guardian connection, never an orphaned CLI subprocess."""
    def __init__(self, fan, rpm, demo):
        self.fan, self.rpm, self.demo = fan, rpm, demo
        self.cancelled = threading.Event()
        self.messages = queue.Queue()
        self.started = self.confirmed = self.restored = False
        self.reason = self.error = self.exit_code = None
        self.fresh_until = time.monotonic() + 3
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        hw = None
        code = 0
        try:
            if self.demo:
                from .demo import demo_hardware
                from .guardian import run_session
                hw = demo_hardware()
                def wait_stop(delay):
                    return self.cancelled.wait(delay) or time.monotonic() >= self.fresh_until
                run_session(hw, runtime_directory(True), [self.fan], self.rpm, 3600, False,
                            self.messages.put, wait_stop)
            else:
                from .helper import request
                code = request("set", fans=[self.fan], rpm=self.rpm, duration=3600,
                               experimental_ftst=False, lease_seconds=5, output=self.messages.put,
                               cancel_event=self.cancelled, heartbeat=lambda: time.monotonic() < self.fresh_until)
        except Exception as exc:
            error = exc if isinstance(exc, AgenticFanError) else AgenticFanError("RULE_CONTROL_ERROR", str(exc))
            self.messages.put(failure(error, self.demo))
            code = error.exit_code
        finally:
            if hw:
                hw.close()
            self.messages.put({"exit": code})

    def poll(self):
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if "exit" in message:
                self.exit_code = message["exit"]
            if message.get("event") == "control_started":
                self.started = True
            if message.get("event") == "control_confirmed":
                self.confirmed = True
            data = message.get("data", {})
            if data.get("automatic_mode_verified") is True:
                self.restored = True
                self.reason = data.get("reason")
            if message.get("error"):
                self.error = message["error"]
        return self.exit_code is not None

    def cancel(self):
        self.cancelled.set()

    def refresh(self):
        self.fresh_until = time.monotonic() + 3


class Runner:
    def __init__(self, path, demo=False, control_factory=Control):
        self.path, self.demo, self.control_factory = Path(path), demo, control_factory
        self.document = self.evaluator = self.control = None
        self.pause = None
        self.automatic_verified = True  # No outstanding write owned by this runner.
        self.last_fan = None
        self.hooks = HookDispatcher()

    def tick(self, sample, now, fresh=True):
        error = None
        try:
            doc = load(self.path)
            if self.document is None or doc.generation != self.document.generation:
                if self.document and (self.document.config.get("hook") != doc.config.get("hook")
                                      or not doc.config["enabled"]):
                    self.hooks.cancel()
                if self.evaluator is None:
                    self.evaluator = Evaluator(doc.config)
                else:
                    self.evaluator.reconfigure(doc.config)
                self.document = doc
                self.pause = None
            config = self.document.config
            if fresh:
                decision = self.evaluator.evaluate(sample, now)
            else:
                self.evaluator = Evaluator(config)
                decision = {"desired_rpm": None, "active_rule_ids": [], "cooling": False,
                            "readings": {}, "rule_states": [], "readings_available": False}
                error = "Temperature sampling exceeded the three-second freshness window; control was released."
        except (OSError, AgenticFanError, ValueError) as exc:
            decision = {"desired_rpm": None, "active_rule_ids": [], "cooling": False, "readings": {}, "rule_states": []}
            config = None
            error = str(exc)
            self.document = self.evaluator = None
            self.hooks.cancel()
        self.last_fan = next((fan for fan in sample.get("fans", [])
                              if fan["id"] == (config or {}).get("fan", getattr(self.control, "fan", -1))), None)
        if self.control and self.control.poll():
            ended = self.control
            # Readback can independently establish auto even after an error
            # envelope (which is never promoted into successful actuation).
            old_fan = next((fan for fan in sample.get("fans", []) if fan["id"] == ended.fan), None)
            verified = ended.restored or bool(old_fan and old_fan.get("mode") == "auto")
            self.automatic_verified = verified or (not ended.started and ended.exit_code in (0, 2, 3, 4, 6))
            expected_cancel = (ended.cancelled.is_set() and verified and
                               (ended.error or {}).get("code") == "CONTROL_CANCELLED")
            if (ended.error and not expected_cancel) or ended.exit_code not in (0, 130):
                self.pause = (ended.error or {}).get("message", "Rule control did not complete successfully.")
            elif ended.reason == "external_auto":
                self.pause = "An external automatic-mode request paused these rules. Reapply or enable them explicitly to resume."
            if not self.automatic_verified:
                self.pause = "Automatic restoration was not verified. Restore automatic mode and inspect fan state."
            self.control = None
        desired = decision["desired_rpm"] if not error and not self.pause else None
        if self.control and (desired != self.control.rpm or (config and config["fan"] != self.control.fan)):
            self.control.cancel()
        elif self.control and desired is not None and not self.control.cancelled.is_set():
            self.control.refresh()
        if desired is not None and self.control is None:
            if not self.last_fan or self.last_fan.get("mode") != "auto":
                self.pause = "Fan is already controlled manually or its mode is unknown; rules did not take over."
            elif self.automatic_verified:
                self.control = self.control_factory(config["fan"], desired, self.demo)
                self.automatic_verified = False
        if self.control and self.control.cancelled.is_set():
            state = "restoring"
        elif error:
            state = "error"
        elif self.pause:
            state = "paused"
        elif not config["enabled"]:
            state = "disabled"
        elif not decision.get("readings_available", True):
            state, error = "paused", "No valid identified temperature reading is available for the enabled rules."
        elif self.control:
            state = "cooling" if decision["cooling"] else "active"
        elif decision.get("pending"):
            state = "pending"
        else:
            state = "watching"
        confirmed = bool(self.control and self.control.confirmed and config
                         and self.control.fan == config["fan"]
                         and self.control.rpm == decision["desired_rpm"]
                         and not self.control.cancelled.is_set())
        if config and not error:
            for rule in config["rules"]:
                if rule["id"] in decision.get("triggered_rule_ids", []):
                    self.hooks.submit(config.get("hook"), {
                        "version": 1, "event": "rule_triggered", "event_id": str(uuid.uuid4()),
                        "timestamp": time.time(), "simulated": self.demo, "rule": rule,
                        "temperature_celsius": decision["readings"].get(rule["sensor_group"]),
                        "fan": config["fan"], "target_rpm": decision["desired_rpm"],
                        "control_status": state, "control_confirmed": confirmed,
                    })
        return {"enabled": bool(config and config["enabled"]), "status": state,
                "config_path": str(self.path), "config": config,
                "loaded_revision": self.document.revision if self.document else None,
                "loaded_generation": self.document.generation if self.document else None,
                **decision, "error": error or self.pause,
                "hook": self.hooks.status(),
                "control_confirmed": confirmed,
                "automatic_mode_verified": self.automatic_verified,
                "control_active": self.control is not None}

    def stop(self, read_fans):
        try:
            return self._stop_control(read_fans)
        finally:
            self.hooks.close()

    def _stop_control(self, read_fans):
        if not self.control:
            return self.automatic_verified
        control = self.control
        control.cancel()
        control.thread.join(timeout=10)
        completed = control.poll()
        verified = control.restored
        if completed and not verified:
            try:
                fan = next((f for f in read_fans() if f["id"] == control.fan), None)
                verified = bool(fan and fan.get("mode") == "auto")
            except (OSError, AgenticFanError):
                pass
        if completed and not control.started and control.exit_code in (0, 2, 3, 4, 6):
            verified = True  # Rejected before writes; do not override another owner.
        self.automatic_verified = bool(completed and verified)
        return self.automatic_verified


def run(path, hw, sample, output, *, demo=False, parent_watch=False, count=None):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    runner = Runner(path, demo)
    stopped = threading.Event()
    previous = {}
    reason = "stopped"
    code = 0
    latest = {"enabled": False, "status": "starting", "config_path": str(path)}
    broken_output = False
    def stop(*_):
        stopped.set()
    def wait(delay):
        if parent_watch:
            readable, _, _ = select.select([0], [], [], delay)
            if readable:
                os.read(0, 1)  # EOF or a stop byte both revoke the parent lease.
                stopped.set()
            return stopped.is_set()
        return stopped.wait(delay)
    def publish(state):
        atomic_json(state_path(path), {**state, "running": True, "pid": os.getpid(),
                                      "updated_at_unix": time.time(), "simulated": demo})
    with lock(path.parent, "." + path.name + ".runner.lock", blocking=False):
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, stop)
        try:
            index = 0
            while not stopped.is_set() and (count is None or index < count):
                started = time.monotonic()
                # Observe a closed parent before evaluating an enabled rule.
                if wait(0):
                    reason = "parent_disconnected"
                    break
                current = sample(hw)
                observed = time.monotonic()
                latest = runner.tick(current, observed, fresh=observed - started <= 3)
                publish(latest)
                output(success({**current, "rules": latest}, demo, "sample"))
                index += 1
                if count is None or index < count:
                    wait(max(0, 1 - (time.monotonic() - started)))
        except BrokenPipeError:
            broken_output, reason = True, "consumer_disconnected"
        except (OSError, AgenticFanError, ValueError) as exc:
            code, reason = 5, "monitoring_failed"
            if not broken_output:
                try:
                    output(failure(exc if isinstance(exc, AgenticFanError) else AgenticFanError("RULE_MONITOR_ERROR", str(exc)), demo))
                except BrokenPipeError:
                    broken_output = True
        finally:
            verified = runner.stop(hw.fans)
            if not verified:
                code = 7
            latest = {**latest, "status": "stopped" if verified else "error", "running": False,
                      "hook": runner.hooks.status(),
                      "automatic_mode_verified": verified, "control_active": False if verified else latest.get("control_active"),
                      "pid": os.getpid(), "updated_at_unix": time.time(), "simulated": demo}
            try:
                atomic_json(state_path(path), latest)
            except OSError:
                if code == 0:
                    code = 5
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            if not broken_output:
                try:
                    output(success({"automatic_mode_verified": verified, "reason": reason}, demo, "rules_finished"))
                except BrokenPipeError:
                    pass
    return code
