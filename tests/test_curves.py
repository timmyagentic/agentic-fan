"""Curve behavior and live retargeting, with no physical hardware writes."""
from copy import deepcopy
import tempfile
from pathlib import Path
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from agenticfan import rules
from agenticfan.errors import AgenticFanError
from tests.test_rules import FakeControl, sample
from tests.test_core import FakeClock
from agenticfan.demo import MemorySMC
from agenticfan.hardware import Hardware
from agenticfan.guardian import LeaseInput, run_session


def configuration():
    value = deepcopy(rules.DEFAULT)
    value.update(enabled=True, mode="curve", curve={
        "sensor_group": "cpu", "ramp_rpm_per_second": 200,
        "nodes": [{"temperature_celsius": 60, "target_rpm": 1200},
                  {"temperature_celsius": 80, "target_rpm": 2200},
                  {"temperature_celsius": 90, "target_rpm": 3200},
                  {"temperature_celsius": 100, "target_rpm": 4500}],
    })
    return value


class CurveTests(unittest.TestCase):
    def test_interpolates_nodes_and_holds_endpoints(self):
        evaluator = rules.Evaluator(configuration())
        for index, (temperature, expected) in enumerate([(40, 1200), (60, 1200), (80, 2200),
                                                         (85, 2700), (95, 3850), (120, 4500)]):
            result = evaluator.evaluate(sample(temperature), index)
            self.assertEqual(result["desired_rpm"], expected)
            self.assertEqual(result["triggered_rule_ids"], [])

    def test_rejects_duplicate_unsorted_descending_and_nonfinite_nodes(self):
        for nodes in [[], [{"temperature_celsius": 60, "target_rpm": 1200}],
                      [{"temperature_celsius": 80, "target_rpm": 1200}, {"temperature_celsius": 80, "target_rpm": 2200}],
                      [{"temperature_celsius": 90, "target_rpm": 1200}, {"temperature_celsius": 80, "target_rpm": 2200}],
                      [{"temperature_celsius": 60, "target_rpm": 2200}, {"temperature_celsius": 80, "target_rpm": 1200}],
                      [{"temperature_celsius": 60, "target_rpm": True}, {"temperature_celsius": 80, "target_rpm": 2200}],
                      [{"temperature_celsius": float('nan'), "target_rpm": 1200}, {"temperature_celsius": 80, "target_rpm": 2200}]]:
            conf = configuration(); conf["curve"]["nodes"] = nodes
            with self.subTest(nodes=nodes), self.assertRaises(AgenticFanError):
                rules.validate(conf)

    def test_missing_readings_release_instead_of_using_raw_values(self):
        evaluator = rules.Evaluator(configuration())
        self.assertIsNotNone(evaluator.evaluate(sample(85), 0)["desired_rpm"])
        for state in ("invalid", "unverified", "unavailable"):
            result = evaluator.evaluate(sample(100, status=state), 1)
            self.assertIsNone(result["desired_rpm"])
            self.assertFalse(result["readings_available"])

    def test_every_node_must_fit_the_selected_fan_before_control(self):
        conf = configuration(); conf["curve"]["nodes"][-1]["target_rpm"] = 12000
        with self.assertRaises(AgenticFanError):
            rules.Evaluator(conf).evaluate(sample(60), 0)

    def test_legacy_configuration_roundtrips_unchanged(self):
        self.assertEqual(rules.validate(rules.DEFAULT), rules.DEFAULT)
        conf = configuration(); conf["enabled"] = False
        self.assertIsNone(rules.Evaluator(conf).evaluate(sample(100), 0)["desired_rpm"])

    def test_live_curve_ramps_from_observed_speed_without_restarting_control(self):
        class Retargetable(FakeControl):
            def __init__(self, *args):
                super().__init__(*args)
                self.updates = []

            def update(self, rpm):
                self.rpm = rpm; self.confirmed = False; self.updates.append(rpm)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'rules.json'
            rules.save(path, configuration())
            runner = rules.Runner(path, True, Retargetable)
            first_sample = sample(100)
            first_sample['fans'][0].update(actual_rpm=1200, target_rpm=1200)
            first = runner.tick(first_sample, 0)
            self.assertEqual(first['desired_rpm'], 1200)
            control = runner.control
            hot = sample(100, mode='manual')
            hot['fans'][0].update(actual_rpm=1200, target_rpm=1200)
            self.assertEqual(runner.tick(hot, 1)['desired_rpm'], 1400)
            self.assertEqual(runner.tick(hot, 2)['desired_rpm'], 1600)
            cool = sample(40, mode='manual')
            self.assertEqual(runner.tick(cool, 3)['desired_rpm'], 1400)
            self.assertIs(runner.control, control)
            self.assertFalse(control.cancelled.is_set())
            self.assertEqual(control.updates, [1400, 1600, 1400])
            conf = configuration(); conf['enabled'] = False
            rules.save(path, conf)
            runner.tick(cool, 4)
            self.assertTrue(control.cancelled.is_set())

    def test_current_target_confirmation_ignores_old_target_receipts(self):
        # Exercise receipt consumption without starting a control thread.
        import queue
        control = rules.Control.__new__(rules.Control)
        control.messages = queue.Queue(); control.rpm = 3200; control.confirmed = False
        control.exit_code = None; control.sequence = 2
        control.messages.put({'event': 'control_confirmed', 'data': {'target_rpm': 3000}})
        control.poll()
        self.assertFalse(control.confirmed)
        control.messages.put({'event': 'control_confirmed', 'data': {'target_rpm': 3200, 'sequence': 0}})
        control.poll()
        self.assertFalse(control.confirmed, 'An earlier visit to the same RPM cannot confirm a new command')
        control.messages.put({'event': 'control_confirmed', 'data': {'target_rpm': 3200, 'sequence': 2}})
        control.poll()
        self.assertTrue(control.confirmed)


class RetargetTests(unittest.TestCase):
    def test_fragmented_frames_are_bounded_and_cannot_select_fans_or_keys(self):
        channel = LeaseInput()
        self.assertTrue(channel.feed(b'.{"rp'))
        channel.feed(b'm":3200,"sequence":1}\n{"rpm":3400,"sequence":2}\n')
        self.assertEqual(channel.take_target(), {'rpm': 3400, 'sequence': 2})
        self.assertIsNone(channel.take_target())
        for frame in [b'{"rpm":true}\n', b'{"rpm":0}\n', b'{"rpm":3,"fan":1}\n',
                      b'{"rpm":3,"rpm":4}\n', b'{"key":"F0Md"}\n', b'{"rpm":' + b'1' * 130]:
            with self.subTest(frame=frame), self.assertRaises(AgenticFanError):
                LeaseInput().feed(frame)

    def test_targets_change_without_mode_switches_or_extending_deadline(self):
        smc = MemorySMC(); hw = Hardware(smc, simulated=True); clock = FakeClock(); events = []
        with tempfile.TemporaryDirectory() as temporary:
            run_session(hw, Path(temporary), [0], 3000, 4, False, events.append,
                        clock.wait, clock.now, clock.now,
                        target_update=lambda: {'rpm': 3000 + int(clock.time) * 200, 'sequence': int(clock.time)} if clock.time >= 1 else None)
        targets = [e['data']['target_rpm'] for e in events if e['event'] == 'control_target_updated']
        self.assertEqual(targets, [3200, 3400, 3600])
        self.assertEqual([raw for key, raw in smc.writes if key == 'F0Md'], [b'\1', b'\0'])
        self.assertEqual(clock.time, 4)
        self.assertTrue(events[-1]['data']['automatic_mode_verified'])

    def test_out_of_range_update_restores_and_does_not_write_invalid_target(self):
        smc = MemorySMC(); hw = Hardware(smc, simulated=True); clock = FakeClock()
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(AgenticFanError):
            run_session(hw, Path(temporary), [0], 3000, 4, False, lambda _: None,
                        clock.wait, clock.now, clock.now, target_update=lambda: {'rpm': 99999, 'sequence': 1})
        self.assertEqual(hw.mode(0), 'auto')
        self.assertEqual(hw.number('F0Tg'), 3000)

    def test_continuous_updates_cannot_hide_stalled_actual_rpm(self):
        smc = MemorySMC(); hw = Hardware(smc, simulated=True); clock = FakeClock()
        original = smc.write
        def write(key, raw):
            actual = smc.values['F0Ac']
            original(key, raw)
            smc.values['F0Ac'] = actual
        smc.write = write
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(AgenticFanError) as caught:
            run_session(hw, Path(temporary), [0], 3000, 30, False, lambda _: None,
                        clock.wait, clock.now, clock.now,
                        target_update=lambda: {'rpm': 3000 + min(14, int(clock.time)) * 200, 'sequence': int(clock.time) + 1})
        self.assertEqual(caught.exception.code, 'CONTROL_NOT_CONFIRMED')
        self.assertLessEqual(clock.time, 16)
        self.assertEqual(hw.mode(0), 'auto')


class CurveTransportTests(unittest.TestCase):
    def test_cli_ramps_on_one_live_session_and_finishes_in_automatic_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / 'rules.json'
            config.write_text(json.dumps(configuration()))
            environment = dict(os.environ, AGENTICFAN_DEMO_DIR=str(root / 'runtime'))
            result = subprocess.run([sys.executable, '-m', 'agenticfan', '--demo', 'rules', 'run',
                                     '--config', str(config), '--count', '6'], capture_output=True,
                                    text=True, env=environment, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            messages = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertTrue(all(m['simulated'] and m['ok'] for m in messages))
            samples = [m['data'] for m in messages if m.get('event') == 'sample']
            goals = [s['rules']['desired_rpm'] for s in samples]
            self.assertEqual(goals[0], 1800)
            self.assertEqual(goals[-1], 1200)
            self.assertGreater(len(set(goals)), 2)
            self.assertTrue(all(abs(a - b) < 220 for a, b in zip(goals, goals[1:])))
            self.assertTrue(all(s['rules']['status'] == 'active' for s in samples))
            self.assertTrue(samples[-1]['rules']['control_confirmed'])
            self.assertTrue(messages[-1]['data']['automatic_mode_verified'])
            state = json.loads((root / 'runtime/state.json').read_text())
            self.assertEqual(state['F0Md'], '00')


if __name__ == '__main__':
    unittest.main()
