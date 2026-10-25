"""Native presentation contracts and the platform-neutral launcher boundary."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agenticfan import gui, helper_install
from agenticfan.errors import AgenticFanError

ROOT = Path(__file__).resolve().parents[1]


class NativeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'Login item tests require the macOS Swift toolchain')
    def test_native_login_item_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = str(Path(temporary) / 'login-items')
            subprocess.run(['swiftc', str(ROOT / 'native/LoginItem.swift'), str(ROOT / 'native/LoginItemTests.swift'),
                            '-o', binary], check=True, capture_output=True)
            result = subprocess.run([binary], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_frozen_gui_passes_explicit_backend_and_demo_flags_without_shell(self):
        with patch.object(sys, 'platform', 'darwin'), patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', '/Applications/A quoted app/AgenticFan.app/Contents/MacOS/agenticfan'), \
                patch.object(Path, 'is_file', return_value=True), patch.object(os, 'execv') as execute:
            gui.main(demo=True, smoke_test=True)
        binary, arguments = execute.call_args.args
        self.assertEqual(binary, '/Applications/A quoted app/AgenticFan.app/Contents/MacOS/AgenticFanUI')
        self.assertEqual(arguments, [binary, '--backend', '/Applications/A quoted app/AgenticFan.app/Contents/MacOS/agenticfan', '--demo', '--smoke'])

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'Native model tests require the macOS Swift toolchain')
    def test_native_model_contracts(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = str(Path(temporary) / 'models')
            subprocess.run(['swiftc', '-g', str(ROOT / 'native/Models.swift'), str(ROOT / 'native/Command.swift'), str(ROOT / 'native/ModelTests.swift'), '-o', binary], check=True, capture_output=True)
            with subprocess.Popen([binary, '--backend', sys.executable], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                try:
                    stdout, stderr = process.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    try:
                        sample = subprocess.run(['sample', str(process.pid), '1', '10'], capture_output=True, text=True, timeout=8)
                        diagnostic = sample.stdout + sample.stderr
                    except (OSError, subprocess.TimeoutExpired) as error:
                        diagnostic = str(error)
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    self.fail(f"Native probe timed out. stdout={stdout!r}, stderr={stderr!r}\n{diagnostic}")
                self.assertEqual(process.returncode, 0, stderr.decode(errors='replace'))
                example_line = next(line for line in stdout.decode().splitlines() if line.startswith('HOOK_EXAMPLES_JSON:'))
                examples = json.loads(example_line.partition(':')[2])
                self.assertTrue(json.loads(examples[0])['simulated'])
                compile(examples[1], '<native-hook-python-example>', 'exec')
                shell = subprocess.run(['/bin/sh', '-n'], input=examples[2].encode(), capture_output=True)
                self.assertEqual(shell.returncode, 0, shell.stderr.decode(errors='replace'))

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'Native memory tests require the macOS Swift toolchain')
    def test_native_stream_memory_stays_bounded_with_slow_consumer(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([
                sys.executable, str(ROOT / 'scripts/test-native-memory.py'),
                '--output-dir', temporary, '--frames', '1000', '--timeout', '60',
            ], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['passed'])
            run = report['runs'][-1]
            self.assertTrue(run['warmup']['producer_running'])
            self.assertTrue(run['after_replay']['producer_running'])
            self.assertEqual(run['last_sample_sequence'], 1100)


class HelperUpgradeTests(unittest.TestCase):
    def test_waits_for_removal_after_asynchronous_bootout(self):
        replies = [subprocess.CompletedProcess([], n) for n in (0, 0, 0, 113)]
        with patch.object(helper_install, 'run', side_effect=replies) as run, patch.object(helper_install.time, 'sleep') as sleep:
            helper_install.stop_helper()
        self.assertEqual(run.call_args_list[-1].args[1], 'print')
        self.assertEqual(sleep.call_count, 2)

    def test_stop_timeout_prevents_replacement(self):
        with patch.object(helper_install, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                patch.object(helper_install.time, 'monotonic', side_effect=[0, 11]), self.assertRaises(AgenticFanError) as error:
            helper_install.stop_helper()
        self.assertEqual(error.exception.code, 'HELPER_STOP_FAILED')

    def test_lookup_error_does_not_mean_service_removed(self):
        with patch.object(helper_install, 'run', return_value=subprocess.CompletedProcess([], 5)), self.assertRaises(AgenticFanError):
            helper_install.stop_helper()
