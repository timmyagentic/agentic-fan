"""Root-side ownership and cutover from the launchd job installed by 0.4.5."""
import json
import os
from pathlib import Path
import pwd
import plistlib
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager

from .errors import AgenticFanError
from .helper import BASE, CONFIG, LEGACY_PLIST, LEGACY_LABEL, LEGACY_EXECUTABLE, SOCKET, check_root_path
from .runtime import lock, runtime_directory
from .service_auth import verify_form


def owner():
    if not CONFIG.exists():
        return None
    check_root_path(CONFIG)
    uid = json.loads(CONFIG.read_text())['authorized_uid']
    if type(uid) is not int or uid < 501:
        raise AgenticFanError('UNSAFE_HELPER_PATH', 'Invalid existing control authorization.', 4)
    return uid


def launchctl(*args):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, timeout=12).returncode


def legacy_loaded():
    result = launchctl('print', 'system/' + LEGACY_LABEL)
    if result not in (0, 3, 113):
        raise AgenticFanError('SERVICE_MIGRATION_FAILED', 'Cannot verify the previous service state.')
    return result == 0


def migrate_legacy():
    if not LEGACY_PLIST.exists():
        if legacy_loaded():
            raise AgenticFanError('SERVICE_MIGRATION_FAILED', 'Unexpected legacy job without its definition.')
        return
    check_root_path(LEGACY_PLIST)
    definition = plistlib.loads(LEGACY_PLIST.read_bytes())
    if (owner() is None or definition.get('Label') != LEGACY_LABEL or
            definition.get('ProgramArguments') != [str(LEGACY_EXECUTABLE), '--internal-helper']):
        raise AgenticFanError('SERVICE_MIGRATION_FAILED', 'Unrecognized existing service; left untouched.')
    backup = BASE / 'legacy-launchd.plist'
    # A v1 guardian holds this SAME lock throughout apply, control and restore.
    with lock(runtime_directory(), 'session.lock', blocking=False):
        was_loaded = legacy_loaded()
        try:
            if was_loaded:
                if launchctl('bootout', 'system/' + LEGACY_LABEL) != 0:
                    raise AgenticFanError('SERVICE_MIGRATION_FAILED', 'Previous service did not stop.')
                deadline = time.monotonic() + 10
                while legacy_loaded():
                    if time.monotonic() >= deadline:
                        raise AgenticFanError('SERVICE_MIGRATION_FAILED', 'Previous service is still loaded.')
                    time.sleep(.1)
            # Retain the old root-owned binary and definition for rollback. The
            # new service never executes them or touches the user's rule files.
            LEGACY_PLIST.replace(backup)
        except Exception:
            if was_loaded and LEGACY_PLIST.exists() and not legacy_loaded():
                if launchctl('bootstrap', 'system', str(LEGACY_PLIST)) != 0:
                    raise AgenticFanError('SERVICE_ROLLBACK_FAILED', 'Could not restart previous service.')
            raise


class ServiceAccess:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = 0
        self.drain_until = 0.
        self.legacy = LEGACY_PLIST.exists() or legacy_loaded()

    def authorize(self, uid):
        granted = owner()
        if granted is not None and uid not in (0, granted):
            raise AgenticFanError('HELPER_ACCESS_DENIED', 'Another user owns fan control.', 4)
        return granted

    def state(self, uid):
        with self.lock:
            granted = self.authorize(uid)
            return ('migration_required' if self.legacy else 'authorization_required' if granted is None
                    else 'updating' if time.monotonic() < self.drain_until else 'ready')

    def permissions(self):
        uid = owner()
        os.chown(SOCKET, uid if uid is not None else 0, -1)
        # Before enrollment ONLY status and administrator-proven enrollment are
        # accepted. Once enrolled, both Unix permissions and peer UID apply.
        os.chmod(SOCKET, 0o600 if uid is not None else 0o666)

    def manage(self, uid, request):
        with self.lock:
            granted = self.authorize(uid)
            op = request['operation']
            if op == 'enroll':
                if self.legacy and granted is None:
                    raise AgenticFanError('SERVICE_MIGRATION_FAILED', 'Missing previous owner; refusing to reassign.')
                if granted is None:
                    if uid < 501:
                        raise AgenticFanError('HELPER_ACCESS_DENIED', 'A regular local account is required.', 4)
                    pwd.getpwuid(uid)
                    verify_form(request['authorization'])
                    # Create a complete root-owned grant without replacing one.
                    with tempfile.NamedTemporaryFile(dir=BASE) as staged:
                        staged.write((json.dumps({'authorized_uid': uid}) + '\n').encode())
                        staged.flush(); os.fsync(staged.fileno())
                        os.link(staged.name, CONFIG)
                    self.permissions()
                return
            if granted is None:
                raise AgenticFanError('HELPER_ACCESS_DENIED', 'Authorize fan control first.', 4)
            if self.active:
                raise AgenticFanError('CONTROL_BUSY', 'Finish the active control session before updating.', 6)
            if op == 'migrate':
                if self.legacy:
                    migrate_legacy()
                    self.legacy = False
            elif op == 'drain':
                if not self.legacy:
                    with lock(runtime_directory(), 'session.lock', blocking=False):
                        pass  # also detect a surviving guardian from a previous daemon
                self.drain_until = time.monotonic() + 90
            elif op == 'resume':
                self.drain_until = 0
            elif op == 'revoke':
                verify_form(request['authorization'])
                with lock(runtime_directory(), 'session.lock', blocking=False):
                    if self.legacy:
                        raise AgenticFanError('SERVICE_MIGRATION_FAILED', 'Migrate the previous job before removal.')
                    CONFIG.unlink()
                    self.permissions()

    @contextmanager
    def control(self, uid):
        with self.lock:
            if self.state(uid) != 'ready':
                raise AgenticFanError('SERVICE_NOT_READY', 'Fan control is not authorized or is being updated.', 4)
            self.active += 1
        try:
            yield
        finally:
            with self.lock: self.active -= 1
