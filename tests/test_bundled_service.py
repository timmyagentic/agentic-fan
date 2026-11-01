"""Service lifecycle at the OS boundary; no registration, user prompts or real fan writes."""
from contextlib import ExitStack
import json
from pathlib import Path
import plistlib
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agenticfan import helper, helper_install as lifecycle, service_access as access
from agenticfan.errors import AgenticFanError
from agenticfan.runtime import lock


class MigrationAndAccessTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        base = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.config = base / 'helper.json'; self.legacy = base / 'legacy.plist'
        self.runtime = base / 'run'; self.runtime.mkdir()
        self.loaded = False; self.actions = []
        def ctl(op, *args):
            if op == 'print': return 0 if self.loaded else 113
            self.actions.append(op)
            self.loaded = op == 'bootstrap'
            return 0
        for name, value in [('BASE', base), ('CONFIG', self.config), ('LEGACY_PLIST', self.legacy)]:
            stack.enter_context(patch.object(access, name, value))
        stack.enter_context(patch.object(access, 'launchctl', side_effect=ctl))
        stack.enter_context(patch.object(access, 'runtime_directory', return_value=self.runtime))
        # These files belong to the test user, not root. Production checks stay enabled.
        stack.enter_context(patch.object(access, 'check_root_path'))
        stack.enter_context(patch.object(access.ServiceAccess, 'permissions'))

    def old_service(self):
        self.loaded = True
        self.config.write_text('{"authorized_uid":501,"version":"0.4.5"}')
        self.legacy.write_bytes(plistlib.dumps({'Label': helper.LEGACY_LABEL,
            'ProgramArguments': [str(helper.LEGACY_EXECUTABLE), '--internal-helper']}))
        return access.ServiceAccess()

    def test_migration_waits_for_legacy_guardian_and_preserves_original_owner(self):
        manager = self.old_service()
        old = self.config.read_bytes()
        with lock(self.runtime, 'session.lock'), self.assertRaises(AgenticFanError) as caught:
            manager.manage(501, {'operation':'migrate'})
        self.assertEqual(caught.exception.code, 'CONTROL_BUSY')
        self.assertTrue(self.loaded)
        with self.assertRaises(AgenticFanError):
            with manager.control(501): self.fail('two generations could write')
        self.assertEqual(self.actions, [])
        manager.manage(501, {'operation':'migrate'})
        self.assertEqual(self.actions, ['bootout'])
        self.assertEqual(manager.state(501), 'ready')
        self.assertEqual(self.config.read_bytes(), old)
        self.assertFalse(self.legacy.exists())
        self.assertTrue((access.BASE / 'legacy-launchd.plist').is_file())
        with self.assertRaises(AgenticFanError): manager.authorize(502)

    def test_migration_rolls_back_bootout_on_archive_failure(self):
        manager = self.old_service()
        with patch.object(Path, 'replace', side_effect=OSError('injected move failure')), self.assertRaises(OSError):
            manager.manage(501, {'operation':'migrate'})
        self.assertEqual(self.actions, ['bootout','bootstrap'])
        self.assertTrue(self.loaded and self.legacy.exists())
        self.assertEqual(manager.state(501), 'migration_required')

    def test_unrecognized_definition_missing_owner_and_foreign_owner_fail_closed(self):
        manager = self.old_service()
        with self.assertRaises(AgenticFanError): manager.manage(502, {'operation':'migrate'})
        self.config.unlink()
        with self.assertRaises(AgenticFanError): manager.manage(502, {'operation':'enroll'})
        self.config.write_text('{"authorized_uid":501}')
        self.legacy.write_bytes(plistlib.dumps({'Label':'someone.else'}))
        with self.assertRaises(AgenticFanError): manager.manage(501, {'operation':'migrate'})
        self.assertEqual(self.actions, [])
        self.assertTrue(self.loaded)

    def test_enrollment_validates_admin_right_and_binds_only_the_kernel_peer(self):
        manager = access.ServiceAccess()
        self.assertEqual(manager.state(501), 'authorization_required')
        with self.assertRaises(AgenticFanError):
            with manager.control(501): self.fail('unbound client could write')
        form = 'A' * 43 + '='
        with patch.object(access.pwd,'getpwuid'), patch.object(access,'verify_form',side_effect=AgenticFanError('DENIED','Denied')):
            with self.assertRaises(AgenticFanError): manager.manage(501, {'operation':'enroll','authorization':form})
        self.assertFalse(self.config.exists())
        a,b = socket.socketpair(); self.addCleanup(a.close)
        with patch.object(helper,'peer_uid',return_value=501), patch.object(access.pwd,'getpwuid'), patch.object(access,'verify_form') as verify:
            helper.send(a, {'protocol':2,'operation':'enroll','authorization':form})
            helper.handle(b, manager, threading.Event())
            self.assertEqual(json.loads(a.makefile().readline())['data']['state'],'ready')
            verify.assert_called_once_with(form)
        self.assertEqual(json.loads(self.config.read_text()), {'authorized_uid':501})
        self.assertNotIn(form,self.config.read_text())
        with self.assertRaises(AgenticFanError): manager.manage(502, {'operation':'enroll','authorization':form})
        with self.assertRaises(AgenticFanError): helper.validate_request({'protocol':2,'operation':'enroll','authorization':form,'uid':501})

    def test_active_session_blocks_revoke_and_drain_cannot_strand_service_forever(self):
        self.config.write_text('{"authorized_uid":501}'); manager = access.ServiceAccess()
        with manager.control(501):
            for op in ('revoke','drain'):
                with self.assertRaises(AgenticFanError) as caught: manager.manage(501, {'operation':op})
                self.assertEqual(caught.exception.code,'CONTROL_BUSY')
        with patch.object(access.time,'monotonic',return_value=10):
            manager.manage(501, {'operation':'drain'})
            self.assertEqual(manager.state(501),'updating')
            with self.assertRaises(AgenticFanError):
                with manager.control(501): self.fail('writes allowed while stopping')
        with patch.object(access.time,'monotonic',return_value=101):
            self.assertEqual(manager.state(501),'ready')

    def test_failed_launchd_lookup_is_not_absence(self):
        with patch.object(access,'launchctl',return_value=5),self.assertRaises(AgenticFanError):
            access.legacy_loaded()

    def test_bootout_timeout_keeps_definition_and_grant(self):
        manager=self.old_service()
        with patch.object(access,'legacy_loaded',return_value=True), patch.object(access,'launchctl',return_value=0), \
                patch.object(access.time,'monotonic',side_effect=[0,11]), self.assertRaises(AgenticFanError):
            manager.manage(501,{'operation':'migrate'})
        self.assertTrue(self.legacy.is_file() and self.config.is_file())


@unittest.skipUnless(sys.platform == 'darwin', 'macOS Authorization Services')
class AuthorizationTests(unittest.TestCase):
    def test_unprivileged_authorization_ref_cannot_claim_control(self):
        import base64
        import ctypes as C
        from agenticfan.service_auth import security, check, Form, verify_form
        lib = security(); ref = C.c_void_p(); form = Form()
        check(lib.AuthorizationCreate(None, None, 0, C.byref(ref)))  # no rights or UI
        try:
            check(lib.AuthorizationMakeExternalForm(ref, C.byref(form)))
            with self.assertRaises(AgenticFanError) as caught:
                verify_form(base64.b64encode(form).decode())
            self.assertEqual(caught.exception.code, 'SERVICE_AUTHORIZATION_DENIED')
        finally:
            lib.AuthorizationFree(ref, 0)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for obj, key, val in [(sys,'platform','darwin'),(sys,'frozen',True)]:
            self.stack.enter_context(patch.object(obj,key,val,create=True))
        self.stack.enter_context(patch.object(lifecycle.os,'geteuid',return_value=501))
        self.registration = 'not_registered'
        self.actions = []
        def native(action):
            self.actions.append(action)
            if action == 'register': self.registration='enabled'
            if action == 'unregister': self.registration='not_registered'
            return self.registration
        self.stack.enter_context(patch.object(lifecycle,'native',side_effect=native))
        self.previous = self.stack.enter_context(patch.object(lifecycle,'previous_opt_in',return_value=False))
        self.stack.enter_context(patch.object(lifecycle,'bundle_build',return_value='new'))
        self.state = dict(running=True,compatible=True,ready=True,state='ready',service_build='new')
        self.wait=self.stack.enter_context(patch.object(lifecycle,'wait_service',side_effect=lambda *a: dict(self.state)))
        self.rpc=self.stack.enter_context(patch.object(lifecycle,'rpc'))
        self.stack.enter_context(patch.object(helper,'status',side_effect=lambda:dict(self.state)))

    def test_monitor_only_and_system_opt_out_never_trigger_registration(self):
        for registration in ('not_registered','requires_approval','not_installed'):
            self.registration=registration
            self.assertFalse(lifecycle.manage('reconcile')['ready'])
        self.assertEqual(self.actions,['status']*3)
        self.wait.assert_not_called()

    def test_legacy_opt_in_registers_then_migrates_and_reports_readiness(self):
        self.previous.return_value=True; self.state.update(state='migration_required',ready=False)
        self.rpc.side_effect=lambda op: self.state.update(state='ready',ready=True)
        state=lifecycle.manage('reconcile')
        self.assertTrue(state['ready']); self.assertEqual(self.actions,['status','register'])
        self.rpc.assert_called_once_with('migrate')

    def test_compatible_current_worker_needs_no_new_authorization_or_reinstall(self):
        self.registration='enabled'
        self.assertTrue(lifecycle.manage('install')['ready'])
        self.rpc.assert_not_called()
        self.assertEqual(self.actions,['status'])

    def test_busy_worker_not_unregistered_and_ready_replacement_rebinds(self):
        self.registration='enabled';self.state['service_build']='old'
        self.rpc.side_effect=AgenticFanError('CONTROL_BUSY','in use',6)
        with self.assertRaises(AgenticFanError): lifecycle.manage('reconcile')
        self.assertNotIn('unregister',self.actions)
        self.rpc.side_effect=lambda op: self.state.update(service_build='new')
        self.assertTrue(lifecycle.manage('reconcile')['ready'])
        self.assertEqual(self.actions[-2:],['unregister','register'])

    def test_system_approval_pending_is_not_successful_control(self):
        with patch.object(lifecycle,'native',side_effect=['not_registered','requires_approval']):
            report=lifecycle.manage('install')
        self.assertFalse(report['ready']);self.assertEqual(report['state'],'requires_approval')
        self.wait.assert_not_called()

    def test_prepare_and_resume_update_preserve_opt_in(self):
        self.registration='enabled'
        self.assertTrue(lifecycle.manage('prepare-update')['resume_after_update'])
        self.assertEqual(self.registration,'not_registered')
        self.assertTrue(lifecycle.manage('resume-update')['ready'])
        self.assertEqual(self.actions,['status','unregister','status','register'])
