"""User-side lifecycle for the service shipped in AgenticFan.app.

Read-only installations never register a daemon. A previous opt-in is reconciled
on upgrade; system approval and the grant to a particular local user are distinct.
"""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

from . import helper
from .errors import AgenticFanError, invalid
from .service_auth import administrator_form


def native(action):
    executable = Path(sys.executable).parent / 'AgenticFanUI'
    result = subprocess.run([str(executable), '--control-service', action],
                            capture_output=True, text=True, timeout=60)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        raise AgenticFanError('SERVICE_REGISTRATION_FAILED', 'Could not read the macOS service state.') from None
    if result.returncode:
        raise AgenticFanError('SERVICE_REGISTRATION_FAILED', report.get('message', 'macOS rejected the registration.'))
    return report['state']


def describe():
    report = helper.status()
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False) and os.geteuid() != 0:
        registration = native('status')
        report['registration'] = registration
        if registration != 'enabled':
            report.update(ready=False, state=registration)
    return report


def bundle_build():
    info = Path(sys.executable).parents[1] / 'Info.plist'
    return plistlib.loads(info.read_bytes())['AgenticFanServiceBuild']


def rpc(op, **fields):
    messages = []
    code = helper.request(op, output=messages.append, **fields)
    if code or not messages or not messages[-1].get('ok'):
        error = messages[-1].get('error', {}) if messages else {}
        raise AgenticFanError(error.get('code', 'SERVICE_NOT_READY'),
                              error.get('message', 'The control service did not acknowledge the request.'), code or 5)
    return messages[-1]['data']


def wait_service(expected_build=None):
    deadline = time.monotonic() + 8
    while True:
        state = helper.status()
        if state.get('running') and (expected_build is None or state.get('service_build') == expected_build):
            return state
        if (state.get('error') or {}).get('code') == 'HELPER_ACCESS_DENIED' or time.monotonic() >= deadline:
            raise AgenticFanError('SERVICE_NOT_READY', (state.get('error') or {}).get('message', 'Control service is not ready.'))
        time.sleep(.15)


def previous_opt_in():
    if not helper.LEGACY_PLIST.exists():
        return False
    # Do not resurrect a disabled legacy daemon merely because its plist exists.
    return subprocess.run(['/bin/launchctl', 'print', 'system/' + helper.LEGACY_LABEL],
                          capture_output=True, timeout=5).returncode == 0


def suspend(registration):
    if registration == 'enabled':
        state = wait_service()
        if state['state'] != 'authorization_required':
            rpc('drain')  # refuses new writes during the unregister window
        try:
            native('unregister')
        except Exception:
            if state['state'] != 'authorization_required':
                try: rpc('resume')
                except (AgenticFanError, OSError): pass  # original unregister error is authoritative
            raise
    # A disabled/pending registration is a system choice. Leave it untouched.


def manage(action):
    if sys.platform != 'darwin' or not getattr(sys, 'frozen', False) or os.geteuid() == 0:
        raise invalid('Use the installed AgenticFan app as your normal user.')
    registration = native('status')
    if registration not in ('enabled', 'requires_approval', 'not_registered', 'not_found'):
        if action in ('uninstall', 'resume-update'):
            raise AgenticFanError('SERVICE_NOT_READY', 'Cannot manage a service from this app location or build.', service_state=registration)
        return {'registration': registration, 'state': registration, 'running': False, 'ready': False, 'resume_after_update': False}
    if action == 'prepare-update':
        suspend(registration)
        return {'resume_after_update': registration == 'enabled', 'previous_state': registration}
    if action == 'uninstall':
        if registration == 'enabled':
            state = wait_service()
            if state['state'] == 'migration_required':
                rpc('migrate')
            if state['state'] != 'authorization_required':
                with administrator_form() as proof:
                    rpc('revoke', authorization=proof)
        if helper.LEGACY_PLIST.exists():
            raise AgenticFanError('SERVICE_MIGRATION_REQUIRED', 'Authorize the app to migrate the previous service first.')
        if registration in ('enabled', 'requires_approval'):
            native('unregister')
        return {'registered': False, 'running': False, 'ready': False, 'state': 'not_registered'}
    if action not in ('install', 'reconcile', 'resume-update'):
        raise invalid('Unknown service action.')
    if registration in ('not_registered', 'not_found') and (
            action in ('install', 'resume-update') or previous_opt_in()):
        registration = native('register')
    if registration != 'enabled':
        return {'registration': registration, 'state': registration, 'running': False, 'ready': False}
    state = wait_service()
    if state.get('service_build') != bundle_build():
        # Finder replacement may have retained an old process. Only refresh an
        # idle service. Compatibility is a protocol check, not a user CLI hash.
        suspend(registration)
        registration = native('register')
        if registration != 'enabled':
            return {'registration': registration, 'state': registration, 'running': False, 'ready': False}
        state = wait_service(bundle_build())
    if state['state'] == 'migration_required':
        rpc('migrate')
        state = wait_service(bundle_build())
    if state['state'] == 'authorization_required' and action == 'install':
        with administrator_form() as proof:
            rpc('enroll', authorization=proof)
        state = wait_service(bundle_build())
    return {**state, 'registration': registration}
