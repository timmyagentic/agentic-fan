import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agenticfan import rules
from agenticfan.errors import AgenticFanError


class HookCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / 'new profile/rules.json'

    def cli(self, action, *args, source=None):
        result = subprocess.run([sys.executable, '-m', 'agenticfan', 'hook', action,
                                 '--config', str(self.config), *args], input=source,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def show(self):
        code, message = self.cli('show')
        self.assertEqual(code, 0, message)
        return message['data']

    def apply(self, content, *options, document=None):
        doc = document or self.show()
        return self.cli('apply', '--stdin', '--if-revision', doc['script']['revision'],
                        '--if-rules-revision', doc['settings']['rules_revision'], *options, source=content)

    def test_show_discovers_default_file_without_creating_anything(self):
        doc = self.show()
        self.assertEqual(doc['script']['path'], str(self.config.parent / 'hooks/threshold-hook.sh'))
        self.assertEqual(doc['script']['revision'], 'missing')
        self.assertTrue(doc['script']['editable'])
        self.assertFalse(doc['script']['exists'])
        self.assertIn('#!/bin/sh', doc['script']['content'])
        self.assertFalse(doc['settings']['enabled'])
        self.assertFalse(self.config.parent.exists())
        self.assertTrue(doc['event_example']['simulated'])

    def test_demo_show_is_read_only_even_without_an_explicit_config(self):
        demo = self.root / 'not-created'
        env = dict(os.environ, AGENTICFAN_DEMO_DIR=str(demo))
        env.pop('AGENTICFAN_RULES_PATH', None)
        result = subprocess.run([sys.executable, '-m', 'agenticfan', '--demo', 'hook', 'show'],
                                env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(json.loads(result.stdout)['simulated'])
        self.assertFalse(demo.exists())

    def test_apply_roundtrip_executable_and_no_implicit_enable(self):
        code, result = self.apply('# a blank action\n')
        self.assertEqual(code, 0, result)
        doc = result['data']['document']
        path = Path(doc['script']['path'])
        self.assertEqual(path.read_text(), '#!/bin/sh\n# a blank action\n')
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(doc['script']['revision'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertFalse(doc['settings']['enabled'])
        self.assertFalse(doc['settings']['rules_enabled'])
        self.assertFalse(result['data']['settings_saved'])
        self.assertEqual(self.show()['script']['content'], path.read_text())
        code, valid = self.cli('validate')
        self.assertEqual(code, 0, valid)
        self.assertTrue(valid['data']['valid'])
        code, status = self.cli('status')
        self.assertEqual(code, 0, status)
        self.assertNotIn('content', status['data']['script'])
        self.assertIsNone(status['data']['execution']['last_observed_revision_matches_current'])

    def test_validation_does_not_run_code_and_missing_file_is_failure(self):
        code, absent = self.cli('validate')
        self.assertEqual(code, 2, absent)
        self.assertEqual(absent['error']['code'], 'HOOK_VALIDATION_FAILED')
        self.assertFalse(self.config.parent.exists())

        marker = self.root / 'must-not-exist'
        code, report = self.cli('validate', '--stdin', source=f'#!/bin/sh\ntouch "{marker}"\n')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['data']['scope'], 'draft')
        self.assertFalse(marker.exists())
        code, invalid = self.cli('validate', '--stdin', source='#!/bin/sh\nif then\n')
        self.assertEqual(code, 2, invalid)
        self.assertFalse(invalid['error']['details']['validation']['valid'])
        self.assertFalse(self.config.parent.exists())

    def test_shebang_checks_do_not_mistake_shell_quoting_or_crlf_for_valid_paths(self):
        for source in ('#!/bin/sh\r\n', '#!"/bin/sh"\n', '#!/usr/bin/env python3 -u\n', '#!/usr/bin/env\n', '#!/usr/bin/env -S\n'):
            code, report = self.cli('validate', '--stdin', source=source)
            self.assertEqual(code, 2, report)

    def test_script_and_rules_conflicts_do_not_overwrite(self):
        self.assertEqual(self.apply('# original\n')[0], 0)
        stale = self.show(); path = Path(stale['script']['path'])
        path.write_text('#!/bin/sh\n# outside edit\n')
        code, conflict = self.apply('# stale\n', document=stale)
        self.assertEqual(code, 6, conflict)
        self.assertEqual(conflict['error']['code'], 'HOOK_CONFLICT')
        self.assertIn('outside edit', path.read_text())
        stale = self.show()
        conf = rules.load(self.config).config
        conf['rules'][0]['trigger_celsius'] = 101
        rules.save(self.config, conf)
        code, conflict = self.apply('# wrong settings version\n', document=stale)
        self.assertEqual(code, 6, conflict)
        self.assertEqual(conflict['error']['code'], 'CONFIG_CONFLICT')
        self.assertIn('outside edit', path.read_text())

    def test_explicit_enable_preserves_other_rules_and_disable_keeps_file(self):
        conf = rules.load(self.config).config
        conf['rules'][0]['trigger_seconds'] = 17
        rules.save(self.config, conf)
        code, result = self.apply('', '--enable', '--timeout-seconds', '15')
        self.assertEqual(code, 0, result)
        saved = rules.load(self.config).config
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['rules'], conf['rules'])
        self.assertEqual(saved['hook']['timeout_seconds'], 15)
        doc = self.show(); path = Path(doc['script']['path'])
        code, result = self.cli('apply', '--disable', '--if-rules-revision', doc['settings']['rules_revision'])
        self.assertEqual(code, 0, result)
        self.assertIsNone(rules.load(self.config).config['hook'])
        self.assertTrue(path.is_file())

    def test_legacy_script_is_edited_in_place_and_nontext_is_preserved(self):
        legacy = self.root / 'custom.py'
        legacy.write_text('#!/usr/bin/env python3\npass\n'); legacy.chmod(0o700)
        conf = rules.load(self.config).config; conf['hook'] = {'program': str(legacy)}
        rules.save(self.config, conf)
        self.assertEqual(self.show()['script']['path'], str(legacy))
        code, result = self.apply('#!/usr/bin/env python3\nx = 1\n')
        self.assertEqual(code, 0, result)
        self.assertIn('x = 1', legacy.read_text())
        legacy.write_bytes(b'\x00\xff')
        doc = self.show()
        self.assertFalse(doc['script']['editable'])
        self.assertIsNone(doc['script']['content'])
        code, default = self.cli('show', '--default')
        self.assertEqual(code, 0, default)
        self.assertNotEqual(default['data']['script']['path'], str(legacy))
        self.assertEqual(legacy.read_bytes(), b'\x00\xff')

    def test_revision_is_required_before_writing(self):
        doc = self.show()
        code, error = self.cli('apply', '--stdin', '--if-rules-revision', doc['settings']['rules_revision'], source='')
        self.assertEqual(code, 2, error)
        self.assertFalse(self.config.parent.exists())

    def test_enabling_an_existing_script_checks_its_revision(self):
        self.assertEqual(self.apply('')[0], 0)
        stale = self.show()
        Path(stale['script']['path']).write_text('#!/bin/sh\n# outside change\n')
        code, error = self.cli('apply', '--enable', '--if-revision', stale['script']['revision'],
                               '--if-rules-revision', stale['settings']['rules_revision'])
        self.assertEqual(code, 6, error)
        self.assertEqual(error['error']['code'], 'HOOK_CONFLICT')
        self.assertFalse(self.show()['settings']['enabled'])

    def demo_hook_receipt(self, body):
        conf = rules.load(self.config).config
        conf['enabled'] = True
        conf['rules'][0].update(trigger_celsius=47, release_celsius=45, trigger_seconds=1)
        rules.save(self.config, conf)
        code, applied = self.apply('#!/bin/sh\ncat > event.json\n' + body, '--enable', '--timeout-seconds', '15')
        self.assertEqual(code, 0, applied)
        env = dict(os.environ, AGENTICFAN_DEMO_DIR=str(self.root / 'demo'))
        env.pop('AGENTICFAN_RULES_PATH', None)
        result = subprocess.run([sys.executable, '-m', 'agenticfan', '--demo', 'rules', 'run',
                                 '--config', str(self.config), '--count', '4'],
                                env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(all(message['simulated'] for message in messages))
        self.assertTrue(messages[-1]['data']['automatic_mode_verified'])
        event = json.loads((self.config.parent / 'hooks/event.json').read_text())
        self.assertTrue(event['simulated'])
        code, status = self.cli('status')
        self.assertEqual(code, 0, status)
        execution = status['data']['execution']
        self.assertFalse(execution['runner_running'])
        self.assertFalse(execution['running'])
        self.assertEqual(execution['last_result']['script_revision'], applied['data']['script_saved_revision'])
        self.assertTrue(execution['last_observed_revision_matches_current'])
        return execution['last_result']

    def test_demo_trigger_returns_execution_revision_through_hook_status(self):
        self.assertEqual(self.demo_hook_receipt('')['status'], 'succeeded')

    def test_runner_stop_persists_final_hook_cancellation_not_stale_running_status(self):
        self.assertEqual(self.demo_hook_receipt('sleep 10\n')['status'], 'cancelled')


class HookFileTests(unittest.TestCase):
    def setUp(self):
        from agenticfan import hook_files
        self.core = hook_files
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / 'rules.json'

    def test_partial_failure_reports_saved_script(self):
        doc = self.core.show(self.config)
        with patch('agenticfan.rules.atomic_json', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(AgenticFanError) as caught:
                self.core.apply(self.config, b'', if_revision=doc['script']['revision'],
                                if_rules_revision=doc['settings']['rules_revision'], enabled=True)
        self.assertEqual(caught.exception.code, 'HOOK_APPLY_PARTIAL')
        self.assertTrue(caught.exception.details['script_saved'])
        self.assertTrue(Path(doc['script']['path']).exists())
        self.assertFalse(self.config.exists())

    def test_post_save_readback_failure_still_reports_committed_changes(self):
        doc = self.core.show(self.config)
        with patch('agenticfan.hook_files.show', side_effect=OSError('readback failed')):
            with self.assertRaises(AgenticFanError) as caught:
                self.core.apply(self.config, b'', if_revision='missing',
                                if_rules_revision=doc['settings']['rules_revision'], enabled=True)
        self.assertEqual(caught.exception.code, 'HOOK_APPLY_PARTIAL')
        self.assertTrue(caught.exception.details['script_saved'])
        self.assertTrue(caught.exception.details['settings_saved'])
        self.assertTrue(self.core.show(self.config)['settings']['enabled'])

    def test_check_never_launches_an_arbitrary_interpreter(self):
        marker = self.root / 'marker'
        interpreter = self.root / 'pretend-python'
        interpreter.write_text(f'#!/bin/sh\ntouch "{marker}"\n'); interpreter.chmod(0o700)
        report = self.core.validate(self.config, f'#!{interpreter}\nanything\n'.encode())
        self.assertTrue(report['valid'])
        self.assertEqual(report['syntax']['status'], 'unsupported')
        self.assertFalse(marker.exists())
        python_name = self.root / 'python3'
        interpreter.rename(python_name)
        report = self.core.validate(self.config, f'#!{python_name}\npass\n'.encode())
        self.assertEqual(report['syntax']['status'], 'passed')
        self.assertFalse(marker.exists(), 'Python syntax must use AST parsing, not execute the declared interpreter')

    def test_shell_validation_ignores_caller_startup_code(self):
        marker = self.root / 'marker'
        startup = self.root / 'startup.sh'
        startup.write_text(f'touch "{marker}"\n')
        with patch.dict(os.environ, {'BASH_ENV': str(startup), 'ENV': str(startup)}):
            report = self.core.validate(self.config, b'#!/bin/bash\nprintf ok\n')
        self.assertTrue(report['valid'])
        self.assertFalse(marker.exists())

    def test_permissions_interpreter_and_unsupported_file_checks(self):
        doc = self.core.show(self.config)
        self.core.apply(self.config, b'#!/bin/sh\n', if_revision='missing',
                        if_rules_revision=doc['settings']['rules_revision'])
        path = Path(doc['script']['path']); path.chmod(0o600)
        report = self.core.validate(self.config)
        self.assertFalse(report['valid'])
        self.assertIn('NOT_EXECUTABLE', [x['code'] for x in report['issues']])
        report = self.core.validate(self.config, b'#!/missing/interpreter\n')
        self.assertFalse(report['valid'])
        path.unlink(); path.symlink_to(self.root / 'not-here')
        self.assertFalse(self.core.show(self.config)['script']['editable'])

    def test_script_target_cannot_replace_its_rules_file(self):
        conf = rules.load(self.config).config; conf['hook'] = {'program': str(self.config)}
        rules.save(self.config, conf)
        before = self.config.read_bytes(); doc = self.core.show(self.config)
        with self.assertRaises(AgenticFanError):
            self.core.apply(self.config, b'#!/bin/sh\n', if_revision=doc['script']['revision'],
                            if_rules_revision=doc['settings']['rules_revision'])
        self.assertEqual(self.config.read_bytes(), before)

    def test_status_compares_current_with_observed_execution_revision(self):
        doc = self.core.show(self.config)
        result = self.core.apply(self.config, b'#!/bin/sh\n', if_revision='missing',
                                 if_rules_revision=doc['settings']['rules_revision'], enabled=True)
        script = result['document']['script']
        runtime = {'running': True, 'status': 'watching', 'hook': {'running': False, 'queued': 0, 'dropped': 0,
            'last_result': {'status': 'succeeded', 'program': script['path'],
                            'script_revision': script['revision'], 'script_revision_after': script['revision']}}}
        with patch('agenticfan.rules.status', return_value=runtime):
            self.assertTrue(self.core.status(self.config)['execution']['last_observed_revision_matches_current'])
            Path(script['path']).write_text('#!/bin/sh\n# changed\n')
            self.assertFalse(self.core.status(self.config)['execution']['last_observed_revision_matches_current'])


if __name__ == '__main__':
    unittest.main()
