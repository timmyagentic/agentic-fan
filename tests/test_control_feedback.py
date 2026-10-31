"""Continuous feedback regressions; all hardware is simulated."""
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from agenticfan import rules
from agenticfan.codecs import KeyInfo
from agenticfan.demo import MemorySMC
from agenticfan.errors import AgenticFanError
from agenticfan.guardian import run_session
from agenticfan.hardware import Hardware
from tests.test_core import FakeClock
from tests.test_curves import configuration
from tests.test_rules import sample


def observation(sequence=0, target=1800, actual=1800, at=1):
    return {'sequence': sequence, 'target_rpm': target, 'actual_rpm': {'0': actual},
            'sampled_monotonic': at, 'sampled_at': '2026-10-31T12:00:00+00:00',
            'tolerance_rpm': max(100, target * .08),
            'within_tolerance': abs(actual - target) <= max(100, target * .08)}


def control(fan=0, rpm=1800, demo=True):
    # Real receipt consumer, but no thread or hardware writes.
    with patch.object(threading.Thread, 'start'):
        return rules.Control(fan, rpm, demo)


class FeedbackTests(unittest.TestCase):
    def feed(self, c, data, now=None):
        c.messages.put({'event': 'control_feedback', 'data': data})
        with patch('agenticfan.rules.time.monotonic', return_value=data['sampled_monotonic'] if now is None else now):
            c.poll()
            return c.feedback_state()

    def test_new_request_preserves_following_without_confirming_new_sequence(self):
        c = control()
        self.assertEqual(self.feed(c, observation())['status'], 'tracking')
        self.assertTrue(c.confirmed)
        c.update(1900)
        state = self.feed(c, observation(at=2))
        self.assertEqual(state['status'], 'tracking')
        self.assertTrue(state['pending_update'])
        self.assertEqual(state['target_rpm'], 1800)
        self.assertFalse(c.confirmed)
        self.assertEqual(self.feed(c, observation(1, 1900, 1901, 3))['status'], 'tracking')
        self.assertTrue(c.confirmed)

    def test_fixed_target_deviation_revokes_confirmation_and_recovers(self):
        c = control()
        self.feed(c, observation())
        self.assertTrue(c.confirmed)
        self.assertEqual(self.feed(c, observation(actual=1200, at=2))['status'], 'adjusting')
        self.assertFalse(c.confirmed)
        self.feed(c, observation(at=3))
        self.assertTrue(c.confirmed)

    def test_delayed_reordered_wrong_fan_and_future_feedback_cannot_claim_health(self):
        c = control()
        self.assertEqual(self.feed(c, observation(), now=5)['status'], 'stale')
        self.assertFalse(c.confirmed)
        self.feed(c, observation(at=6))
        c.update(1900)
        self.feed(c, observation(1, 1900, 1200, 7))
        for bad in [observation(at=8), observation(1, 1900, 1900, 6),
                    observation(2, 2000, 2000, 8), observation(1, 1900, 1900, 99),
                    observation(1, 1900, float('nan'), 8), observation(1, 1900, 1900, float('inf')),
                    observation(1, 2100, 2100, 8),
                    {**observation(1, 1900, 1900, 8), 'actual_rpm': {'1': 1900}}]:
            state = self.feed(c, bad, now=8)
            self.assertEqual(state['status'], 'adjusting')
            self.assertFalse(c.confirmed)
        with patch('agenticfan.rules.time.monotonic', return_value=11):
            self.assertEqual(c.feedback_state()['status'], 'stale')
            self.assertFalse(c.confirmed)

    def test_cancellation_and_new_session_cannot_reuse_old_confirmation(self):
        c = control()
        self.feed(c, observation())
        c.cancel()
        with patch('agenticfan.rules.time.monotonic', return_value=2):
            self.assertEqual(c.feedback_state()['status'], 'stopped')
            self.assertFalse(c.confirmed)
            self.assertEqual(control().feedback_state()['status'], 'waiting')

    def test_runner_continuously_changes_requests_while_reporting_good_feedback(self):
        conf = configuration()
        doc = rules.Document(Path('/not-read'), conf, 'revision', 'generation', False)
        with patch.object(rules, 'load', return_value=doc), patch('agenticfan.rules.time.monotonic') as clock:
            runner = rules.Runner(doc.path, True, control)
            clock.return_value = 0
            runner.tick(sample(100), 0)
            for now in range(1, 6):
                c = runner.control
                c.messages.put({'event': 'control_feedback', 'data': observation(c.sequence, c.rpm, c.rpm, now)})
                clock.return_value = now
                state = runner.tick(sample(100, mode='manual'), now)
                self.assertEqual(state['control_feedback']['status'], 'tracking')
                self.assertTrue(state['control_feedback']['pending_update'])
                self.assertFalse(state['control_confirmed'])

    def test_persisted_status_expires_feedback_even_with_live_runner_heartbeat(self):
        c = control()
        feedback = self.feed(c, observation())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'rules.json'
            rules.atomic_json(rules.state_path(path), {'running': True, 'pid': os.getpid(),
                'updated_at_unix': 100, 'status': 'active', 'control_confirmed': True,
                'control_feedback': feedback})
            with patch('agenticfan.rules.time.time', return_value=101), patch('agenticfan.rules.time.monotonic', return_value=5):
                state = rules.status(path)
                self.assertTrue(state['running'])
                self.assertFalse(state['control_confirmed'])
                self.assertEqual(state['control_feedback']['status'], 'stale')
            with patch('agenticfan.rules.time.time', return_value=105), patch('agenticfan.rules.time.monotonic', return_value=2):
                self.assertFalse(rules.status(path)['control_confirmed'])


class GuardianFeedbackTests(unittest.TestCase):
    def test_observation_precedes_next_write_and_keeps_its_sequence(self):
        clock = FakeClock(); events = []; hw = Hardware(MemorySMC(), simulated=True)
        with tempfile.TemporaryDirectory() as temporary:
            run_session(hw, Path(temporary), [0], 3000, 3, False, events.append,
                clock.wait, clock.now, clock.now,
                target_update=lambda: {'rpm': 3000 + int(clock.time) * 100, 'sequence': int(clock.time)} if clock.time >= 1 else None)
        feedback = [e['data'] for e in events if e['event'] == 'control_feedback']
        self.assertEqual([f['sequence'] for f in feedback], [0, 0, 1, 2])
        self.assertTrue(all(f['actual_rpm']['0'] == f['target_rpm'] for f in feedback))
        self.assertTrue(all(f['within_tolerance'] for f in feedback))
        self.assertTrue(events[-1]['data']['automatic_mode_verified'])

    def test_fixed_target_stalls_after_confirmation_and_restores_at_deadline(self):
        clock = FakeClock(); events = []; smc = MemorySMC(); hw = Hardware(smc, simulated=True)
        def wait(delay):
            clock.wait(delay)
            if clock.time >= 2:
                smc.values['F0Ac'] = (KeyInfo('flt ', 4), struct.pack('<f', 1000))
            return False
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(AgenticFanError) as caught:
            run_session(hw, Path(temporary), [0], 3000, 30, False, events.append,
                        wait, clock.now, clock.now)
        self.assertEqual(caught.exception.code, 'CONTROL_NOT_CONFIRMED')
        self.assertLessEqual(clock.time, 17)
        self.assertEqual(hw.mode(0), 'auto')

    def test_leased_feedback_turns_negative_after_initial_confirmation(self):
        clock = FakeClock(); events = []; smc = MemorySMC(); hw = Hardware(smc, simulated=True)
        def wait(delay):
            clock.wait(delay)
            if clock.time >= 1:
                smc.values['F0Ac'] = (KeyInfo('flt ', 4), struct.pack('<f', 1000))
            return False
        with tempfile.TemporaryDirectory() as temporary:
            run_session(hw, Path(temporary), [0], 3000, 3, False, events.append,
                        wait, clock.now, clock.now, target_update=lambda: None)
        feedback = [e['data']['within_tolerance'] for e in events if e['event'] == 'control_feedback']
        self.assertEqual(feedback, [True, False, False, False])
        self.assertFalse(events[-1]['data']['target_observed'])
        self.assertTrue(events[-1]['data']['automatic_mode_verified'])


if __name__ == '__main__':
    unittest.main()
