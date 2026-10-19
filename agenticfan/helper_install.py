"""One-time, administrator-authorized installation of a root-owned runtime.

Never configure sudoers or retain credentials. Normal hardware requests do not
invoke this module. Updates/removal require the same explicit admin operation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import pwd
import shutil
import subprocess
import sys
import tempfile
import time

from . import __version__
from .errors import AgenticFanError, invalid
from .helper import APP, BASE, CONFIG, EXECUTABLE, LABEL, PLIST, SOCKET, SOCKET_DIRECTORY, check_root_path
from .runtime import lock, runtime_directory

AUTHORIZATION_SCRIPT = '''on run argv
    set commandText to ""
    repeat with argument in argv
        set commandText to commandText & quoted form of (argument as text) & " "
    end repeat
    return do shell script commandText with administrator privileges
end run'''


def run(*arguments, check=True):
    return subprocess.run([str(x) for x in arguments], check=check, capture_output=True, text=True)


def stop_helper():
    """bootout returns before launchd has removed the job; wait before replacing it."""
    run('/bin/launchctl', 'bootout', 'system/' + LABEL, check=False)
    deadline = time.monotonic() + 10
    while True:
        state = run('/bin/launchctl', 'print', 'system/' + LABEL, check=False)
        if state.returncode in (3, 113):  # ESRCH / launchd service-not-found
            return
        if state.returncode != 0:
            raise AgenticFanError('HELPER_STOP_FAILED', 'Cannot verify that the existing helper has stopped.', 4)
        if time.monotonic() >= deadline:
            raise AgenticFanError('HELPER_STOP_FAILED', 'The existing helper did not stop within ten seconds.', 4)
        time.sleep(.1)


def launch_config():
    return {'Label': LABEL, 'ProgramArguments': [str(EXECUTABLE), '--internal-helper'],
            'UserName': 'root', 'RunAtLoad': True, 'KeepAlive': True,
            'AbandonProcessGroup': True, 'ThrottleInterval': 5, 'WorkingDirectory': '/',
            'EnvironmentVariables': {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/var/root',
                                     'PYINSTALLER_RESET_ENVIRONMENT': '1'}}


def secure_bundle(bundle):
    # PyInstaller uses relative symlinks within the bundle. Reject external links
    # before changing ownership; never follow a user-supplied path out of staging.
    root = bundle.resolve()
    paths = [bundle, *bundle.rglob('*')]
    for path in paths:
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise invalid('Helper bundle contains an external symlink.')
        elif not (path.is_dir() or path.is_file()):
            raise invalid('Helper bundle contains a non-regular file.')
    for path in paths:
        os.chown(path, 0, 0, follow_symlinks=False)
        if not path.is_symlink():
            mode = path.stat().st_mode
            os.chmod(path, (mode & 0o755) | (0o755 if path.is_dir() else 0o444))


def installed_owner():
    if CONFIG.exists():
        check_root_path(CONFIG)
        return json.loads(CONFIG.read_text())['authorized_uid']
    return None


def install_privileged(uid):
    if os.geteuid() != 0 or not getattr(sys, 'frozen', False):
        raise AgenticFanError('PERMISSION_DENIED', 'Helper installation requires the bundled app and administrator authorization.', 4)
    if type(uid) is not int or uid < 501:
        raise invalid('Authorize a regular local user.')
    pwd.getpwuid(uid)  # refuse non-existent accounts
    source = Path(sys.executable).resolve().parents[2]
    info = plistlib.loads((source / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != 'io.github.timmyagentic.agenticfan':
        raise invalid('Unexpected application bundle.')
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', source)
    # Validate trusted parent directories before creating or replacing files.
    for parent in (Path('/Library'), Path('/Library/Application Support'), PLIST.parent):
        check_root_path(parent, directory=True)
    BASE.mkdir(mode=0o755, exist_ok=True)
    check_root_path(BASE, directory=True)
    if installed_owner() not in (None, uid):
        raise AgenticFanError('HELPER_ACCESS_DENIED', 'Helper belongs to another local user. Remove it explicitly before changing ownership.', 4)
    with lock(runtime_directory(False), 'session.lock', blocking=False):
        with tempfile.TemporaryDirectory(prefix='.install-', dir=BASE) as temporary:
            staged = Path(temporary) / 'Helper.app'
            shutil.copytree(source, staged, symlinks=True)
            secure_bundle(staged)
            run('/usr/bin/codesign', '--verify', '--deep', '--strict', staged)
            if APP.exists(): check_root_path(APP, directory=True)
            old = BASE / 'Helper.previous.app'
            if old.exists():
                check_root_path(old, directory=True)
                shutil.rmtree(old)
            previous_config = CONFIG.read_bytes() if CONFIG.exists() else None
            previous_plist = PLIST.read_bytes() if PLIST.exists() else None
            if PLIST.exists(): check_root_path(PLIST)
            stop_helper()
            if APP.exists(): APP.rename(old)
            try:
                staged.rename(APP)
                CONFIG.write_text(json.dumps({'authorized_uid': uid, 'version': __version__}) + '\n')
                os.chmod(CONFIG, 0o600)
                PLIST.write_bytes(plistlib.dumps(launch_config()))
                os.chmod(PLIST, 0o644)
                run('/bin/launchctl', 'enable', 'system/' + LABEL)
                run('/bin/launchctl', 'bootstrap', 'system', PLIST)
                from .helper import status
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    current = status()
                    if current.get('running') and current.get('compatible'):
                        return {**current, 'authorized_uid': uid, 'hardware_settings_changed': False}
                    time.sleep(.2)
                raise AgenticFanError('HELPER_START_FAILED', 'Installed helper did not become ready.')
            except Exception:
                stop_helper()
                if APP.exists(): shutil.rmtree(APP)
                if old.exists(): old.rename(APP)
                if previous_config is not None: CONFIG.write_bytes(previous_config)
                else: CONFIG.unlink(missing_ok=True)
                if previous_plist is not None:
                    PLIST.write_bytes(previous_plist)
                    run('/bin/launchctl', 'bootstrap', 'system', PLIST, check=False)
                else: PLIST.unlink(missing_ok=True)
                raise


def uninstall_privileged(uid):
    if os.geteuid() != 0:
        raise AgenticFanError('PERMISSION_DENIED', 'Administrator authorization is required.', 4)
    if not BASE.exists(): return {'installed': False}
    check_root_path(BASE, directory=True)
    if installed_owner() not in (None, uid):
        raise AgenticFanError('HELPER_ACCESS_DENIED', 'Helper belongs to another user.', 4)
    with lock(runtime_directory(False), 'session.lock', blocking=False):
        stop_helper()
        if PLIST.exists():
            check_root_path(PLIST)
            PLIST.unlink()
        for bundle in (APP, BASE / 'Helper.previous.app'):
            if bundle.exists():
                check_root_path(bundle, directory=True)
                shutil.rmtree(bundle)
        CONFIG.unlink(missing_ok=True)
        if SOCKET_DIRECTORY.exists():
            check_root_path(SOCKET_DIRECTORY, directory=True)
            SOCKET.unlink(missing_ok=True)
            SOCKET_DIRECTORY.rmdir()
    return {'installed': False, 'hardware_settings_changed': False}


def manage(action):
    if sys.platform != 'darwin' or not getattr(sys, 'frozen', False):
        raise invalid('Run helper installation/removal using the installed AgenticFan.app CLI on macOS.')
    if os.geteuid() == 0:
        raise invalid('Run helper install as your normal user; the system will request authorization once.')
    if action == 'install':
        from .helper import status
        current = status()
        if current.get('running') and current.get('compatible'):
            return current  # idempotent; never prompt if already enabled
    command = [sys.executable, '--internal-helper-' + action, str(os.getuid())]
    result = run('/usr/bin/osascript', '-e', AUTHORIZATION_SCRIPT, *command, check=False)
    if result.returncode:
        raise AgenticFanError('HELPER_INSTALL_FAILED', 'System authorization or helper setup failed.', 4,
                          diagnostic=result.stderr.strip())
    messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if not messages or not messages[-1].get('ok'):
        raise AgenticFanError('HELPER_INSTALL_FAILED', 'Helper did not confirm setup.', 4)
    return messages[-1]['data']
