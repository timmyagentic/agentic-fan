import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agenticfan import __version__, helper
from agenticfan.errors import AgenticFanError


def request(operation='set', **changes):
    data = {'protocol': 1, 'version': __version__, 'build': helper.build_id(), 'operation': operation}
    if operation in ('set', 'auto'):
        data.update(fans=[0], experimental_ftst=False)
    if operation == 'set':
        data.update(rpm=3000, duration=1)
    data.update(changes)
    return data


class HelperProtocolTests(unittest.TestCase):
    def test_no_commands_paths_keys_or_demo_parameters(self):
        for extra in ({'command': 'id'}, {'executable': '/bin/sh'}, {'key': 'F0Md'}, {'demo': True},
                      {'fans': [True]}, {'fans': [-1]}, {'fans': [0, 0]}, {'fans': 'all'},
                      {'duration': float('nan')}, {'duration': 3601}, {'rpm': True},
                      {'lease_seconds': True}, {'lease_seconds': 0}, {'lease_seconds': 11}, {'lease_seconds': float('nan')},
                      {'rpm': 0}, {'experimental_ftst': 1}, {'operation': 'exec'}):
            with self.subTest(extra=extra), self.assertRaises(AgenticFanError):
                helper.validate_request(request(**extra))

    def test_rules_lease_is_bounded_and_optional(self):
        helper.validate_request(request(lease_seconds=5))
        helper.validate_request(request())

    def test_version_mismatch_rejected_before_hardware(self):
        with self.assertRaises(AgenticFanError) as error:
            helper.validate_request(request(version='0.0.0'))
        self.assertEqual(error.exception.code, 'HELPER_VERSION_MISMATCH')
        helper.validate_request(request('status', version='0.0.0'))

    def test_uid_boundary_uses_kernel_credentials(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close); self.addCleanup(server.close)
        self.assertEqual(helper.peer_uid(server), os.geteuid())
        with patch('agenticfan.helper.peer_uid', return_value=9999):
            with self.assertRaises(AgenticFanError) as error:
                helper.authorize(server, 501)
            self.assertEqual(error.exception.code, 'HELPER_ACCESS_DENIED')

    def test_handler_denies_other_user_before_spawning(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        with patch('agenticfan.helper.peer_uid', return_value=9999), patch('agenticfan.helper.relay') as relay:
            helper.handle(server, 501, threading.Event())
        message = json.loads(client.makefile('r').readline())
        self.assertFalse(message['ok'])
        relay.assert_not_called()

    def test_oversized_request_rejected(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close); self.addCleanup(server.close)
        client.sendall(b'x' * (helper.MAX_MESSAGE + 2) + b'\n')
        with self.assertRaises(AgenticFanError): helper.read_request(server)

    def test_worker_uses_only_installed_executable_and_fixed_arguments(self):
        self.assertEqual(helper.worker_command(request()), [str(helper.EXECUTABLE), '--internal-guardian'])
        self.assertEqual(helper.worker_command(request('auto')), [str(helper.EXECUTABLE), 'fans', 'auto', '--fan', '0'])

    def test_missing_helper_never_falls_back_to_sudo(self):
        with tempfile.TemporaryDirectory() as tmp, patch('agenticfan.helper.SOCKET_DIRECTORY', Path(tmp) / 'absent'), \
                patch('agenticfan.helper.subprocess.Popen') as popen:
            with self.assertRaises(AgenticFanError) as error:
                helper.request('set', fans=[0], rpm=3000, duration=1, experimental_ftst=False)
            self.assertEqual(error.exception.code, 'HELPER_NOT_INSTALLED')
            popen.assert_not_called()


class HelperRelayTests(unittest.TestCase):
    def test_leased_retarget_crosses_socket_and_guardian_then_restores(self):
        # Replace only the test worker's initial backend selection with explicit
        # demo. Exercise real relay framing, guardian parsing and final receipts.
        script = '''from agenticfan import guardian
original = guardian.json.loads
def synthetic(data, *args, **kwargs):
 value = original(data, *args, **kwargs)
 if isinstance(value, dict) and 'fans' in value and 'duration' in value:
  value['demo'] = True
 return value
guardian.json.loads = synthetic
raise SystemExit(guardian.main())
'''
        with tempfile.TemporaryDirectory() as temporary:
            client, server = socket.socketpair()
            self.addCleanup(client.close)
            errors = []
            def work():
                try:
                    helper.relay(server, request(duration=30, lease_seconds=5), threading.Event(),
                                 command=[sys.executable, '-c', script],
                                 env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                                          AGENTICFAN_DEMO_DIR=temporary))
                except Exception as exc:
                    errors.append(exc)
                finally:
                    server.close()
            thread = threading.Thread(target=work, daemon=True)
            thread.start(); client.settimeout(8)
            stream = client.makefile('r'); self.addCleanup(stream.close)
            started = json.loads(stream.readline())
            self.assertEqual(started['event'], 'control_started')
            self.assertTrue(started['simulated'])
            client.sendall(b'.{"rpm":3200,"sequence":1}\n')
            feedback = []
            while True:
                message = json.loads(stream.readline())
                if message.get('event') == 'control_feedback':
                    feedback.append(message['data'])
                if message.get('event') == 'control_confirmed' and message['data'].get('sequence') == 1:
                    self.assertEqual(message['data']['target_rpm'], 3200)
                    self.assertEqual(message['data']['actual_rpm']['0'], 3200)
                    break
                self.assertTrue(message.get('ok'), message)
            self.assertEqual(feedback[-1]['sequence'], 1)
            self.assertEqual(feedback[-1]['target_rpm'], 3200)
            self.assertEqual(feedback[-1]['actual_rpm']['0'], 3200)
            self.assertTrue(feedback[-1]['within_tolerance'])
            client.shutdown(socket.SHUT_WR)
            finished = None
            while True:
                message = json.loads(stream.readline())
                if message.get('event') == 'control_finished': finished = message
                if 'helper_exit_code' in message:
                    self.assertEqual(message['helper_exit_code'], 0)
                    break
            thread.join(8)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(finished['data']['automatic_mode_verified'])
            self.assertEqual(json.loads((Path(temporary) / 'state.json').read_text())['F0Md'], '00')

    def test_socket_disconnect_closes_guardian_pipe_and_restores(self):
        # Real transport/subprocess exercise with an explicitly simulated SMC.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            client, server = socket.socketpair()
            script = '''import json,sys
from agenticfan.guardian import run_session
from agenticfan.demo import demo_hardware
from agenticfan.runtime import runtime_directory
from agenticfan.protocol import emit
import select,os
config=json.loads(sys.stdin.readline())
def stopped(t):
 if select.select([sys.stdin],[],[],t)[0]:
  os.read(sys.stdin.fileno(),1)
  return True
 return False
run_session(demo_hardware(),runtime_directory(True),config['fans'],config['rpm'],30,False,emit,stopped)
'''
            root = str(Path(__file__).resolve().parents[1])
            errors = []
            def work():
                try:
                    helper.relay(server, request(duration=30), threading.Event(),
                                 command=[sys.executable, '-c', script],
                                 env=dict(os.environ, PYTHONPATH=root, AGENTICFAN_DEMO_DIR=tmp))
                except (BrokenPipeError, ConnectionResetError): pass
                except Exception as exc: errors.append(exc)
                finally: server.close()
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            client.settimeout(5)
            stream = client.makefile('r')
            started = json.loads(stream.readline())
            self.assertEqual(started['event'], 'control_started')
            stream.close(); client.close()
            thread.join(8)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            state = json.loads((directory / 'state.json').read_text())
            self.assertEqual(state['F0Md'], '00')

    def test_server_shutdown_cancels_live_guardian(self):
        # Protocol boundary invariant: daemon shutdown must close the keepalive
        # pipe even when the caller remains connected.
        client, server = socket.socketpair()
        self.addCleanup(client.close); self.addCleanup(server.close)
        stopping = threading.Event(); stopping.set()
        script = "import sys,json;sys.stdin.readline();sys.stdin.read();print(json.dumps({'ok':True,'event':'control_finished','data':{'automatic_mode_verified':True}}),flush=True)"
        thread = threading.Thread(target=helper.relay, args=(server, request(), stopping),
                                  kwargs={'command': [sys.executable, '-c', script], 'env': dict(os.environ)}, daemon=True)
        thread.start(); client.settimeout(5)
        stream = client.makefile('r'); self.addCleanup(stream.close)
        self.assertEqual(json.loads(stream.readline())['event'], 'control_finished')
        self.assertEqual(json.loads(stream.readline())['helper_exit_code'], 0)
        thread.join(5); self.assertFalse(thread.is_alive())

class HelperInstallTests(unittest.TestCase):
    def test_bundle_rejects_external_symlink_before_chown(self):
        from agenticfan.helper_install import secure_bundle
        with tempfile.TemporaryDirectory() as tmp, patch('agenticfan.helper_install.os.chown') as chown:
            bundle = Path(tmp) / 'App'; bundle.mkdir()
            (bundle / 'outside').symlink_to('/etc')
            with self.assertRaises(AgenticFanError): secure_bundle(bundle)
            chown.assert_not_called()

    def test_current_helper_install_does_not_prompt(self):
        from agenticfan.helper_install import manage
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'darwin'), \
                patch('agenticfan.helper_install.os.geteuid', return_value=501), \
                patch('agenticfan.helper.status', return_value={'running': True, 'compatible': True}), \
                patch('agenticfan.helper_install.run') as run:
            self.assertTrue(manage('install')['running'])
            run.assert_not_called()

    def test_launchd_executes_protected_runtime_and_preserves_guardian(self):
        from agenticfan.helper_install import launch_config
        config = launch_config()
        self.assertEqual(config['ProgramArguments'], [str(helper.EXECUTABLE), '--internal-helper'])
        self.assertEqual(config['UserName'], 'root')
        self.assertTrue(config['AbandonProcessGroup'])
