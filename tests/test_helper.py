import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from macfan import __version__, helper
from macfan.errors import MacFanError


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
            with self.subTest(extra=extra), self.assertRaises(MacFanError):
                helper.validate_request(request(**extra))

    def test_rules_lease_is_bounded_and_optional(self):
        helper.validate_request(request(lease_seconds=5))
        helper.validate_request(request())

    def test_version_mismatch_rejected_before_hardware(self):
        with self.assertRaises(MacFanError) as error:
            helper.validate_request(request(version='0.0.0'))
        self.assertEqual(error.exception.code, 'HELPER_VERSION_MISMATCH')
        helper.validate_request(request('status', version='0.0.0'))

    def test_uid_boundary_uses_kernel_credentials(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close); self.addCleanup(server.close)
        self.assertEqual(helper.peer_uid(server), os.geteuid())
        with patch('macfan.helper.peer_uid', return_value=9999):
            with self.assertRaises(MacFanError) as error:
                helper.authorize(server, 501)
            self.assertEqual(error.exception.code, 'HELPER_ACCESS_DENIED')

    def test_handler_denies_other_user_before_spawning(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        with patch('macfan.helper.peer_uid', return_value=9999), patch('macfan.helper.relay') as relay:
            helper.handle(server, 501, threading.Event())
        message = json.loads(client.makefile('r').readline())
        self.assertFalse(message['ok'])
        relay.assert_not_called()

    def test_oversized_request_rejected(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close); self.addCleanup(server.close)
        client.sendall(b'x' * (helper.MAX_MESSAGE + 2) + b'\n')
        with self.assertRaises(MacFanError): helper.read_request(server)

    def test_worker_uses_only_installed_executable_and_fixed_arguments(self):
        self.assertEqual(helper.worker_command(request()), [str(helper.EXECUTABLE), '--internal-guardian'])
        self.assertEqual(helper.worker_command(request('auto')), [str(helper.EXECUTABLE), 'fans', 'auto', '--fan', '0'])

    def test_missing_helper_never_falls_back_to_sudo(self):
        with tempfile.TemporaryDirectory() as tmp, patch('macfan.helper.SOCKET_DIRECTORY', Path(tmp) / 'absent'), \
                patch('macfan.helper.subprocess.Popen') as popen:
            with self.assertRaises(MacFanError) as error:
                helper.request('set', fans=[0], rpm=3000, duration=1, experimental_ftst=False)
            self.assertEqual(error.exception.code, 'HELPER_NOT_INSTALLED')
            popen.assert_not_called()


class HelperRelayTests(unittest.TestCase):
    def test_socket_disconnect_closes_guardian_pipe_and_restores(self):
        # Real transport/subprocess exercise with an explicitly simulated SMC.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            client, server = socket.socketpair()
            script = '''import json,sys
from macfan.guardian import run_session
from macfan.demo import demo_hardware
from macfan.runtime import runtime_directory
from macfan.protocol import emit
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
                                 env=dict(os.environ, PYTHONPATH=root, MACFAN_DEMO_DIR=tmp))
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
        from macfan.helper_install import secure_bundle
        with tempfile.TemporaryDirectory() as tmp, patch('macfan.helper_install.os.chown') as chown:
            bundle = Path(tmp) / 'App'; bundle.mkdir()
            (bundle / 'outside').symlink_to('/etc')
            with self.assertRaises(MacFanError): secure_bundle(bundle)
            chown.assert_not_called()

    def test_current_helper_install_does_not_prompt(self):
        from macfan.helper_install import manage
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'darwin'), \
                patch('macfan.helper_install.os.geteuid', return_value=501), \
                patch('macfan.helper.status', return_value={'running': True, 'compatible': True}), \
                patch('macfan.helper_install.run') as run:
            self.assertTrue(manage('install')['running'])
            run.assert_not_called()

    def test_launchd_executes_protected_runtime_and_preserves_guardian(self):
        from macfan.helper_install import launch_config
        config = launch_config()
        self.assertEqual(config['ProgramArguments'], [str(helper.EXECUTABLE), '--internal-helper'])
        self.assertEqual(config['UserName'], 'root')
        self.assertTrue(config['AbandonProcessGroup'])
