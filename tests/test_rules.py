from copy import deepcopy
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from agenticfan import rules
from agenticfan.errors import AgenticFanError


def config(**changes):
    value = deepcopy(rules.DEFAULT)
    value["enabled"] = True
    value.update(changes)
    return value


def sample(cpu=100, *, status="ok", mode="auto", extra=None):
    sensors = [{"id": "cpu", "group": "cpu", "status": status, "value": cpu if status == "ok" else None,
                "raw_value": cpu}]
    components = [{"group": "cpu", "sensor_ids": ["cpu"], "value": cpu}]
    if extra:
        sensors.extend(extra)
    return {"sensors": sensors, "temperature_overview": components,
            "fans": [{"id": 0, "mode": mode, "actual_rpm": 1800, "min_rpm": 1200,
                      "max_rpm": 6550, "control_available": True}]}


class FakeControl:
    def __init__(self, fan, rpm, demo):
        self.fan, self.rpm = fan, rpm
        self.cancelled = threading.Event()
        self.started, self.confirmed, self.restored = True, False, False
        self.reason = self.error = self.exit_code = None
        self.thread = type("Thread", (), {"join": lambda *args, **kwargs: None})()

    def poll(self):
        return self.exit_code is not None

    def cancel(self, reason=None):
        self.local_stop_reason = reason
        self.cancelled.set()

    def refresh(self):
        pass

    def feedback_state(self):
        return None


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "rules.json"

    def test_validation_rejects_bad_inputs_without_replacing_existing_bytes(self):
        rules.save(self.path, config())
        before = self.path.read_bytes()
        for bad in [config(version=True), config(fan=True), config(enabled=1), config(rules=[])]:
            with self.subTest(bad=bad), self.assertRaises(AgenticFanError):
                rules.save(self.path, bad)
            self.assertEqual(self.path.read_bytes(), before)
        for changes in [{"release_celsius": 100}, {"trigger_celsius": float("nan")},
                        {"target_rpm": True}, {"sensor_group": []}, {"release_seconds": -1}]:
            bad = config(); bad["rules"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(AgenticFanError):
                rules.validate(bad)
        for raw in [b'{"version":1,"version":1}', b'{"version":NaN}', b'x' * 65537]:
            with self.assertRaises(AgenticFanError):
                rules.decode(raw)

    def test_atomic_revision_check_prevents_agent_and_gui_lost_update(self):
        first = rules.save(self.path, config())
        external = config(); external["rules"][0]["trigger_celsius"] = 101
        rules.save(self.path, external)
        with self.assertRaises(AgenticFanError) as error:
            rules.save(self.path, config(), first.revision)
        self.assertEqual(error.exception.code, "CONFIG_CONFLICT")
        self.assertEqual(rules.load(self.path).config, external)

    def test_threshold_boundary_and_continuously_observed_hysteresis(self):
        evaluator = rules.Evaluator(config())
        self.assertIsNone(evaluator.evaluate(sample(98.9), 0)["desired_rpm"])
        self.assertEqual(evaluator.evaluate(sample(99), 1)["desired_rpm"], 6550)
        for now in range(2, 7):
            self.assertEqual(evaluator.evaluate(sample(95), now)["desired_rpm"], 6550)
        self.assertIsNone(evaluator.evaluate(sample(95), 7)["desired_rpm"])

    def test_temperature_bounce_and_observation_gap_restart_cool_timer(self):
        evaluator = rules.Evaluator(config())
        evaluator.evaluate(sample(100), 0)
        evaluator.evaluate(sample(94), 1)
        evaluator.evaluate(sample(96), 2)
        evaluator.evaluate(sample(94), 3)
        self.assertEqual(evaluator.evaluate(sample(94), 7)["desired_rpm"], 6550)
        for now in range(8, 12):
            self.assertEqual(evaluator.evaluate(sample(94), now)["desired_rpm"], 6550)
        self.assertIsNone(evaluator.evaluate(sample(94), 12)["desired_rpm"])

    def test_invalid_raw_unknown_and_calibration_points_cannot_trigger(self):
        for state in ("invalid", "unverified", "unavailable"):
            self.assertIsNone(rules.Evaluator(config()).evaluate(sample(999, status=state), 0)["desired_rpm"])
        data = sample(60, extra=[{"id": "cal", "status": "ok", "value": 120,
                                  "exclude_from_overview": True}, {"id": "unknown", "status": "ok", "value": 140}])
        data["temperature_overview"][0]["sensor_ids"].append("cal")
        self.assertIsNone(rules.Evaluator(config()).evaluate(data, 0)["desired_rpm"])

    def test_multiple_rules_select_highest_active_target(self):
        conf = config()
        conf["rules"][0]["target_rpm"] = 5000
        other = deepcopy(conf["rules"][0]); other.update(id="gpu-hot", sensor_group="gpu", target_rpm="max")
        conf["rules"].append(other)
        data = sample(); data["sensors"].append({"id": "gpu", "status": "ok", "value": 100})
        data["temperature_overview"].append({"group": "gpu", "sensor_ids": ["gpu"]})
        self.assertEqual(rules.Evaluator(conf).evaluate(data, 0)["desired_rpm"], 6550)

    def runner(self):
        rules.save(self.path, config())
        return rules.Runner(self.path, True, FakeControl)

    def test_invalid_live_edit_and_missing_sensor_cancel_owned_control(self):
        runner = self.runner()
        runner.tick(sample(), 0)
        self.path.write_text('{"incomplete":')
        state = runner.tick(sample(mode="manual"), 1)
        self.assertEqual(state["status"], "restoring")
        self.assertTrue(runner.control.cancelled.is_set())
        runner = self.runner()
        runner.tick(sample(), 0)
        runner.tick(sample(None, status="unavailable", mode="manual"), 1)
        self.assertTrue(runner.control.cancelled.is_set())

    def test_stale_sample_cancels_control_and_cannot_restart_it(self):
        runner = self.runner()
        runner.tick(sample(), 0)
        state = runner.tick(sample(mode="manual"), 10, fresh=False)
        self.assertEqual(state["status"], "restoring")
        self.assertTrue(runner.control.cancelled.is_set())
        runner = self.runner()
        self.assertEqual(runner.tick(sample(), 0, fresh=False)["status"], "error")
        self.assertIsNone(runner.control)

    def test_external_manual_ownership_is_not_taken_over(self):
        runner = self.runner()
        self.assertEqual(runner.tick(sample(mode="manual"), 0)["status"], "paused")
        self.assertIsNone(runner.control)

    def test_external_auto_is_latched_until_explicit_reapply(self):
        runner = self.runner()
        runner.tick(sample(), 0)
        runner.control.restored = True; runner.control.reason = "external_auto"; runner.control.exit_code = 0
        self.assertEqual(runner.tick(sample(), 1)["status"], "paused")
        self.assertEqual(runner.tick(sample(), 2)["status"], "paused")
        rules.save(self.path, config())
        self.assertEqual(runner.tick(sample(), 3)["status"], "active")

    def test_expected_cancel_during_apply_resumes_edited_target_after_verified_auto(self):
        runner = self.runner()
        runner.tick(sample(), 0)
        changed = config(); changed["rules"][0]["target_rpm"] = 5000
        rules.save(self.path, changed)
        self.assertEqual(runner.tick(sample(mode="manual"), 1)["status"], "restoring")
        runner.control.started = False
        runner.control.error = {"code": "CONTROL_CANCELLED", "message": "cancelled while applying"}
        runner.control.exit_code = 130
        state = runner.tick(sample(), 2)
        self.assertEqual(state["status"], "active")
        self.assertEqual(runner.control.rpm, 5000)

    def test_client_freshness_cancel_during_readback_recovers_only_after_two_fresh_samples(self):
        runner = self.runner()
        runner.tick(sample(), 0)
        ended = runner.control
        ended.local_stop_reason = "sample_timeout"
        ended.error = {"code": "CONTROL_CANCELLED", "message": "cancelled while applying"}
        ended.exit_code = 130
        self.assertEqual(runner.tick(sample(), 5, fresh=False)["status"], "recovering")
        self.assertIsNone(runner.control)
        self.assertEqual(runner.tick(sample(), 6)["status"], "recovering")
        self.assertEqual(runner.tick(sample(), 10)["status"], "recovering", "gap invalidates the streak")
        state = runner.tick(sample(), 11)
        self.assertEqual(state["status"], "active")
        self.assertIsNot(runner.control, ended)
        self.assertEqual(state["last_interruption"], "sample_timeout")

    def test_disabling_during_lease_recovery_stays_disabled(self):
        runner = self.runner(); runner.tick(sample(), 0)
        runner.control.restored = True; runner.control.reason = "lease_expired"; runner.control.exit_code = 0
        rules.save(self.path, config(enabled=False))
        for now in (1, 2, 3):
            self.assertEqual(runner.tick(sample(), now)["status"], "disabled")
            self.assertIsNone(runner.control)

    def test_disable_after_recovery_started_clears_wait_immediately(self):
        runner = self.runner()
        runner.tick(sample(), 0)
        runner.control.restored = True
        runner.control.reason = "lease_expired"
        runner.control.exit_code = 0
        self.assertEqual(runner.tick(sample(), 1)["status"], "recovering")
        rules.save(self.path, config(enabled=False))
        self.assertEqual(runner.tick(sample(), 2)["status"], "disabled")
        self.assertIsNone(runner.control)
        self.assertFalse(runner.recovering)

    def test_final_receipt_can_arrive_after_snapshot_saw_our_manual_fan(self):
        runner = self.runner()
        runner.tick(sample(), 0)
        runner.control.restored = True
        runner.control.reason = "lease_expired"
        runner.control.exit_code = 0
        self.assertEqual(runner.tick(sample(mode="manual"), 1)["status"], "recovering")
        self.assertEqual(runner.tick(sample(), 2)["status"], "recovering")
        self.assertEqual(runner.tick(sample(), 3)["status"], "active")

    def test_lease_expiry_recovers_but_external_stop_and_restore_failure_never_rearm(self):
        for reason, error, exit_code, restored, mode, recover in [
            ("lease_expired", None, 0, True, "auto", True),
            ("external_auto", None, 0, True, "auto", False),
            ("client_disconnected", None, 0, True, "auto", False),
            ("signal", None, 0, True, "auto", False),
            ("lease_expired", {"code":"RESTORE_FAILED","message":"failed"}, 7, False, "auto", False),
            ("lease_expired", None, 0, False, "manual", False),
            (None, {"code":"CONTROL_CANCELLED","message":"unknown cancellation"}, 130, True, "auto", False),
        ]:
            with self.subTest(reason=reason, error=error):
                runner=self.runner(); runner.tick(sample(),0)
                runner.control.restored=restored; runner.control.reason=reason
                runner.control.error=error; runner.control.exit_code=exit_code
                runner.tick(sample(mode=mode),1)
                runner.tick(sample(),2)
                state=runner.tick(sample(),3)
                self.assertEqual(state["status"],"active" if recover else "paused")
                self.assertEqual(runner.control is not None,recover)


class RulesTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "rules.json"
        self.env = dict(os.environ, AGENTICFAN_DEMO_DIR=str(Path(self.temp.name) / "demo"))
        self.base = [sys.executable, "-m", "agenticfan", "--demo"]
        self.root = Path(__file__).resolve().parents[1]

    def test_saved_configuration_is_not_reported_as_running(self):
        result = subprocess.run(self.base + ["rules", "apply", "--config", str(self.path), "--stdin"],
                                input=json.dumps(config()), text=True, capture_output=True, cwd=self.root, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)["data"]
        self.assertTrue(data["saved"])
        self.assertFalse(data["runtime_running"])
        self.assertFalse(data["loaded"])

    def test_disabled_runner_stops_cleanly_when_parent_pipe_closes(self):
        process = subprocess.Popen(self.base + ["rules", "run", "--config", str(self.path), "--parent-watch"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, cwd=self.root, env=self.env)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            first = json.loads(process.stdout.readline())
            self.assertEqual(first["data"]["rules"]["status"], "disabled")
            process.stdin.close(); process.stdin = None
            out, err = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, err)
            self.assertTrue(json.loads(out.splitlines()[-1])["data"]["automatic_mode_verified"])
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()

    def test_live_rule_waits_for_high_temperature_notifies_once_and_restores(self):
        from unittest.mock import patch
        from agenticfan.demo import demo_hardware
        with patch.dict(os.environ, self.env):
            hw = demo_hardware()
            hw.smc.write("TCDX", struct.pack("<f", 100))
            hook = Path(self.temp.name) / 'notify'
            hook.write_text('#!/bin/sh\ncat >> events.jsonl\n'); hook.chmod(0o700)
            conf = config(hook={'program': str(hook)})
            conf['rules'][0]['trigger_seconds'] = 2
            rules.save(self.path, conf)
            process = subprocess.Popen(self.base + ["rules", "run", "--config", str(self.path), "--parent-watch"],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       cwd=self.root, env=self.env)
            try:
                deadline = time.monotonic() + 9
                while time.monotonic() < deadline:
                    live = rules.status(self.path)
                    if live.get("control_confirmed"):
                        break
                    time.sleep(.05)
                else:
                    self.fail("rule did not confirm simulated maximum RPM")
                self.assertEqual(hw.fans()[0]["target_rpm"], 6200)
                self.assertEqual(hw.fans()[0]["mode"], "manual")
                events = (Path(self.temp.name) / 'events.jsonl').read_text().splitlines()
                self.assertEqual(len(events), 1)
                self.assertTrue(json.loads(events[0])['simulated'])
                hw.smc.write("TCDX", struct.pack("<f", 94))
                time.sleep(1.5)
                self.assertEqual(hw.fans()[0]["mode"], "manual", "released before five observed seconds")
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if rules.status(self.path).get("status") == "watching" and hw.fans()[0]["mode"] == "auto":
                        break
                    time.sleep(.05)
                else:
                    self.fail("rule did not restore after its cool-down window")
                process.stdin.close(); process.stdin = None
                _, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual(len((Path(self.temp.name) / 'events.jsonl').read_text().splitlines()), 1)
            finally:
                if process.poll() is None:
                    process.terminate(); process.communicate(timeout=5)
                hw.close()

    def test_guardian_lease_expires_with_pipe_open_and_stops_manual_control(self):
        from agenticfan.launch import guardian_command
        process = subprocess.Popen(guardian_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, cwd=self.root, env=self.env)
        try:
            process.stdin.write(json.dumps({"fans": [0], "rpm": 6000, "duration": 30,
                                           "experimental_ftst": False, "demo": True, "lease_seconds": 1}) + "\n")
            process.stdin.flush()
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            self.assertEqual(json.loads(process.stdout.readline())["event"], "control_started")
            # No EOF and no renewal. The isolated guardian must restore itself.
            self.assertEqual(process.wait(timeout=5), 0)
            messages = [json.loads(line) for line in process.stdout.read().splitlines()]
            self.assertTrue(messages[-1]["data"]["automatic_mode_verified"])
            self.assertEqual(messages[-1]["data"]["reason"], "lease_expired")
            self.assertEqual(json.loads((Path(self.env["AGENTICFAN_DEMO_DIR"]) / "state.json").read_text())["F0Md"], "00")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()


class LeasedControlParameterTests(unittest.TestCase):
    def test_rule_control_has_no_wall_clock_duration(self):
        parameters = rules.leased_control_parameters(0, 1800)
        self.assertEqual(parameters, {"fans": [0], "rpm": 1800, "experimental_ftst": False, "lease_seconds": 5})


if __name__ == "__main__":
    unittest.main()
