from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agenticfan import rules
from agenticfan.errors import AgenticFanError
from agenticfan.rule_hooks import HookDispatcher
from tests.test_rules import config, sample, FakeControl


class TriggerDelayTests(unittest.TestCase):
    def evaluator(self):
        conf = config()
        conf['rules'][0]['trigger_seconds'] = 2
        return rules.Evaluator(conf)

    def test_requires_continuous_high_samples_and_emits_one_transition(self):
        evaluator = self.evaluator()
        for second in [0, 1]:
            state = evaluator.evaluate(sample(100), second)
            self.assertIsNone(state['desired_rpm'])
            self.assertEqual(state['triggered_rule_ids'], [])
        state = evaluator.evaluate(sample(100), 2)
        self.assertEqual(state['triggered_rule_ids'], ['cpu-hot'])
        self.assertEqual(state['desired_rpm'], 6550)
        self.assertEqual(evaluator.evaluate(sample(100), 3)['triggered_rule_ids'], [])
        for second in range(4, 10):
            state = evaluator.evaluate(sample(94), second)
        self.assertIsNone(state['desired_rpm'])
        for second in range(10, 13):
            state = evaluator.evaluate(sample(100), second)
        self.assertEqual(state['triggered_rule_ids'], ['cpu-hot'])

    def test_dip_invalid_sample_and_sleep_gap_reset_hot_timer(self):
        for interruption, when in [(sample(98), 1), (sample(0, status='unverified'), 1),
                                   (sample(100), 5)]:
            with self.subTest(when=when, sample=interruption):
                evaluator = self.evaluator()
                evaluator.evaluate(sample(100), 0)
                self.assertIsNone(evaluator.evaluate(interruption, when)['desired_rpm'])
                self.assertIsNone(evaluator.evaluate(sample(100), when + 1)['desired_rpm'])

    def test_invalid_duration_and_hook_configuration_are_rejected(self):
        for delay in [-1, True, float('nan'), 301]:
            conf = config(); conf['rules'][0]['trigger_seconds'] = delay
            with self.assertRaises(AgenticFanError): rules.validate(conf)
        for hook in [{'program': 'sh -c echo'}, {'program': '/tmp/a', 'timeout_seconds': 0},
                     {'program': '/tmp/a', 'timeout_seconds': True}, {'program': '/tmp/a', 'shell': True}]:
            with self.assertRaises(AgenticFanError): rules.validate(config(hook=hook))

    def test_changed_rule_resets_pending_interval(self):
        evaluator = self.evaluator()
        evaluator.evaluate(sample(100), 0)
        evaluator.evaluate(sample(100), 1)
        updated = deepcopy(evaluator.config)
        updated['rules'][0]['trigger_celsius'] = 99.5
        evaluator.reconfigure(updated)
        for now in (2, 3): self.assertIsNone(evaluator.evaluate(sample(100), now)['desired_rpm'])
        self.assertEqual(evaluator.evaluate(sample(100), 4)['triggered_rule_ids'], ['cpu-hot'])


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def program(self, body):
        path = self.root / 'hook with spaces'
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o700)
        return str(path)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.01)
        self.fail('hook did not finish')

    def test_hook_receives_json_once_and_equivalent_reload_does_not_repeat(self):
        program = self.program('cat >> events.jsonl')
        path = self.root / 'rules.json'
        rules.save(path, config(hook={'program': program}))
        runner = rules.Runner(path, True, FakeControl)
        self.addCleanup(runner.hooks.close)
        first = runner.tick(sample(), 0)
        rules.save(path, config(hook={'program': program}))
        runner.tick(sample(mode='manual'), 1)
        self.wait_for(lambda: runner.hooks.status()['last_result'] is not None)
        events = (self.root / 'events.jsonl').read_text().splitlines()
        self.assertEqual(len(events), 1)
        event = json.loads(events[0])
        self.assertEqual(event['event'], 'rule_triggered')
        self.assertEqual(event['rule']['id'], 'cpu-hot')
        self.assertTrue(event['simulated'])
        self.assertFalse(event['control_confirmed'])
        self.assertTrue(first['control_active'])
        receipt = runner.hooks.status()['last_result']
        self.assertEqual(receipt['program'], program)
        self.assertEqual(receipt['script_revision'], hashlib.sha256(Path(program).read_bytes()).hexdigest())
        self.assertEqual(receipt['script_revision_after'], receipt['script_revision'])
        self.assertFalse(receipt['script_changed_during_execution'])

    def test_execution_revision_reports_changes_without_claiming_an_immutable_snapshot(self):
        program = self.program('sleep 0.3')
        old_revision = hashlib.sha256(Path(program).read_bytes()).hexdigest()
        dispatcher = HookDispatcher(); self.addCleanup(dispatcher.close)
        dispatcher.submit({'program': program}, {'event_id': 'revision-test', 'rule': {'id': 'cpu-hot'}})
        self.wait_for(lambda: dispatcher.status()['running'])
        Path(program).write_text('#!/bin/sh\n# changed while running\n')
        self.wait_for(lambda: dispatcher.status()['last_result'] is not None)
        receipt = dispatcher.status()['last_result']
        self.assertEqual(receipt['script_revision'], old_revision)
        self.assertEqual(receipt['script_revision_after'], hashlib.sha256(Path(program).read_bytes()).hexdigest())
        self.assertTrue(receipt['script_changed_during_execution'])
        self.assertIn('not_an_immutable_snapshot', receipt['revision_evidence'])

    def test_timeout_and_failure_do_not_pause_control_and_leave_no_child(self):
        for body, expected in [('sleep 20', 'timed_out'), ('exit 3', 'failed')]:
            with self.subTest(expected=expected):
                program = self.program(body)
                path = self.root / 'rules.json'
                rules.save(path, config(hook={'program': program, 'timeout_seconds': .2 if expected == 'timed_out' else 2}))
                runner = rules.Runner(path, True, FakeControl)
                try:
                    start = time.monotonic()
                    runner.tick(sample(), 0)
                    self.assertLess(time.monotonic() - start, .2)
                    self.wait_for(lambda: runner.hooks.status()['last_result'] is not None)
                    result = runner.hooks.status()['last_result']
                    self.assertEqual(result['status'], expected)
                    state = runner.tick(sample(mode='manual'), 1)
                    self.assertEqual(state['status'], 'active')
                    self.assertIsNone(state['error'])
                finally: runner.hooks.close()

    def test_pending_timer_survives_noop_reload(self):
        path = self.root / 'rules.json'; conf = config()
        conf['rules'][0]['trigger_seconds'] = 2
        rules.save(path, conf)
        runner = rules.Runner(path, True, FakeControl)
        self.addCleanup(runner.hooks.close)
        self.assertEqual(runner.tick(sample(), 0)['status'], 'pending')
        rules.save(path, deepcopy(conf))
        self.assertEqual(runner.tick(sample(), 1)['status'], 'pending')
        self.assertEqual(runner.tick(sample(), 2)['status'], 'active')

    def test_bounded_queue_cancellation_and_root_refusal(self):
        dispatcher = HookDispatcher(); self.addCleanup(dispatcher.close)
        hook = {'program': self.program('sleep 20'), 'timeout_seconds': 30}
        event = {'event_id': 'id', 'rule': {'id': 'cpu-hot'}}
        dispatcher.submit(hook, event)
        self.wait_for(lambda: dispatcher.status()['running'])
        for _ in range(17): dispatcher.submit(hook, event)
        self.assertEqual(dispatcher.status()['queued'], 16)
        self.assertEqual(dispatcher.status()['dropped'], 1)
        dispatcher.cancel()
        self.wait_for(lambda: not dispatcher.status()['running'])
        self.assertEqual(dispatcher.status()['queued'], 0)
        self.assertEqual(dispatcher.status()['last_result']['status'], 'cancelled')
        with patch('agenticfan.rule_hooks.os.geteuid', return_value=0):
            dispatcher.submit(hook, event)
            self.wait_for(lambda: dispatcher.status()['last_result'].get('reason') == 'root_execution_refused')

    def test_disabling_rules_cancels_hook_and_preserves_control_restoration(self):
        path = self.root / 'rules.json'
        conf = config(hook={'program': self.program('sleep 20')})
        rules.save(path, conf); runner = rules.Runner(path, True, FakeControl)
        self.addCleanup(runner.hooks.close)
        runner.tick(sample(), 0)
        self.wait_for(lambda: runner.hooks.status()['running'])
        conf['enabled'] = False; rules.save(path, conf)
        state = runner.tick(sample(mode='manual'), 1)
        self.assertTrue(runner.control.cancelled.is_set())
        self.assertEqual(state['status'], 'restoring')
        self.wait_for(lambda: not runner.hooks.status()['running'])
        runner.control.restored = True; runner.control.exit_code = 0
        self.assertTrue(runner.stop(lambda: sample()['fans']))

    def test_worker_start_failure_does_not_break_rule_control(self):
        path = self.root / 'rules.json'
        rules.save(path, config(hook={'program': '/missing/program'}))
        runner = rules.Runner(path, True, FakeControl)
        self.addCleanup(runner.hooks.close)
        with patch('agenticfan.rule_hooks.threading.Thread.start', side_effect=RuntimeError('no threads')):
            state = runner.tick(sample(), 0)
        self.assertEqual(state['status'], 'active')
        self.assertEqual(state['hook']['last_result']['reason'], 'worker_start_failed')
        self.assertIsNone(state['error'])

    def test_higher_target_does_not_inherit_previous_control_confirmation(self):
        conf = config(hook={'program': '/not-executed'})
        conf['rules'][0]['target_rpm'] = 5000
        gpu = deepcopy(conf['rules'][0])
        gpu.update(id='gpu-hot', sensor_group='gpu', target_rpm='max')
        conf['rules'].append(gpu)
        path = self.root / 'rules.json'; rules.save(path, conf)
        runner = rules.Runner(path, True, FakeControl)
        self.addCleanup(runner.hooks.close)
        with patch.object(runner.hooks, 'submit') as submit:
            runner.tick(sample(), 0)
            runner.control.confirmed = True
            higher = sample(mode='manual')
            higher['sensors'].append({'id': 'gpu', 'status': 'ok', 'value': 100})
            higher['temperature_overview'].append({'group': 'gpu', 'sensor_ids': ['gpu']})
            state = runner.tick(higher, 1)
            event = submit.call_args.args[1]
        self.assertEqual(event['target_rpm'], 6550)
        self.assertEqual(event['control_status'], 'restoring')
        self.assertFalse(event['control_confirmed'])
        self.assertFalse(state['control_confirmed'])

    def test_timeout_stops_the_hook_child_process_group(self):
        dispatcher = HookDispatcher(); self.addCleanup(dispatcher.close)
        program = self.program('sleep 20 &\necho $! > child.pid\nwait')
        dispatcher.submit({'program': program, 'timeout_seconds': .3},
                          {'event_id': 'child-timeout', 'rule': {'id': 'cpu-hot'}})
        self.wait_for(lambda: (self.root / 'child.pid').exists())
        child = int((self.root / 'child.pid').read_text())
        self.wait_for(lambda: dispatcher.status()['last_result'] is not None)
        self.assertEqual(dispatcher.status()['last_result']['status'], 'timed_out')
        def stopped():
            stat = subprocess.run(['ps', '-o', 'stat=', '-p', str(child)], capture_output=True, text=True).stdout.strip()
            return not stat or stat.startswith('Z')
        self.wait_for(stopped)


class LarkAdapterTests(unittest.TestCase):
    def test_default_dry_run_uses_explicit_identity_and_event_id_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); program = root / 'notify.py'; cli = root / 'fake lark'
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'examples/lark-temperature-hook.py', program)
            cli.write_text(f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nPath(__file__).with_suffix(".args").write_text(json.dumps(sys.argv[1:]))\nprint(json.dumps({{"ok":True}}))\n')
            cli.chmod(0o700)
            program.with_suffix('.json').write_text(json.dumps({'cli': str(cli), 'identity': 'bot', 'chat_id': 'test-chat'}))
            event = {'event': 'rule_triggered', 'event_id': 'test-id', 'simulated': False,
                     'rule': config()['rules'][0], 'temperature_celsius': 101.2,
                     'target_rpm': 4900, 'control_status': 'active', 'control_confirmed': False}
            result = subprocess.run([sys.executable, str(program)], input=json.dumps(event), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads(cli.with_suffix('.args').read_text())
            self.assertEqual(args[-1], '--dry-run')
            self.assertEqual(args[args.index('--idempotency-key') + 1], 'test-id')
            self.assertIn('尚未确认', args[args.index('--text') + 1])
            cli.with_suffix('.args').unlink()
            event['simulated'] = True
            subprocess.run([sys.executable, str(program)], input=json.dumps(event), text=True, check=True)
            self.assertFalse(cli.with_suffix('.args').exists())


if __name__ == '__main__': unittest.main()
