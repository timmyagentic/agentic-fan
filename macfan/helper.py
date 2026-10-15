"""Restricted local control service. No shell, file paths or SMC keys in its protocol.

The administrator grants one local UID access at installation. The socket's
permissions AND kernel peer credentials enforce that grant. This authorizes
processes belonging to that user, including agents; it is not app attestation.
The daemon and every child run from a root-owned copy of the frozen app.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import sys
import threading
from functools import lru_cache

from . import __version__
from .errors import MacFanError, invalid
from .guardian import validate_duration
from .protocol import emit, failure, success

LABEL = 'io.github.timmyagentic.macfan.helper'
BASE = Path('/Library/Application Support/MacFan')
APP = BASE / 'Helper.app'
EXECUTABLE = APP / 'Contents/MacOS/macfan'
CONFIG = BASE / 'helper.json'
PLIST = Path('/Library/LaunchDaemons') / (LABEL + '.plist')
SOCKET_DIRECTORY = Path('/var/run/macfan-helper')
SOCKET = SOCKET_DIRECTORY / 'control.sock'
MAX_MESSAGE = 4096
PROTOCOL = 1


@lru_cache(maxsize=1)
def build_id():
    return hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest() if getattr(sys, 'frozen', False) else 'source'


def peer_uid(connection):
    if sys.platform == 'darwin':
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        uid, gid = ctypes.c_uint32(), ctypes.c_uint32()
        fn = libc.getpeereid
        fn.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        fn.restype = ctypes.c_int
        if fn(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)):
            raise OSError(ctypes.get_errno(), 'Cannot authenticate local peer')
        return uid.value
    # Linux is used exclusively by isolated simulated transport tests.
    import struct
    return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def authorize(connection, allowed_uid):
    uid = peer_uid(connection)
    if uid not in (0, allowed_uid):
        raise MacFanError('HELPER_ACCESS_DENIED', 'This user has not been authorized to control fans.', 4)


def read_request(connection):
    raw = bytearray()
    while len(raw) <= MAX_MESSAGE:
        byte = connection.recv(1)
        if not byte:
            raise invalid('Incomplete helper request.')
        if byte == b'\n':
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError):
                raise invalid('Helper request must be JSON.') from None
        raw.extend(byte)
    raise invalid('Helper request exceeds size limit.')


def validate_request(request):
    if not isinstance(request, dict) or request.get('protocol') != PROTOCOL:
        raise invalid('Unsupported helper protocol.')
    operation = request.get('operation')
    fields = {'protocol', 'operation', 'version', 'build'}
    if operation in ('set', 'auto'):
        fields |= {'fans', 'experimental_ftst'}
        fans = request.get('fans')
        if (not isinstance(fans, list) or not 1 <= len(fans) <= 10 or
                any(type(i) is not int or not 0 <= i <= 9 for i in fans) or len(set(fans)) != len(fans)):
            raise invalid('Helper accepts 1..10 distinct fan IDs.')
        if type(request.get('experimental_ftst')) is not bool:
            raise invalid('experimental_ftst must be boolean.')
    if operation == 'set':
        fields |= {'rpm', 'duration'}
        validate_duration(request.get('duration'))
        if type(request.get('rpm')) is not int or not 1 <= request['rpm'] <= 16383:
            raise invalid('RPM must be a positive supported integer.')
    elif operation not in ('auto', 'status'):
        raise invalid('Helper only accepts status, set and auto.')
    if set(request) != fields:
        raise invalid('Unexpected or missing helper request fields.')
    if (request['version'] != __version__ or request['build'] != build_id()) and operation != 'status':
        raise MacFanError('HELPER_VERSION_MISMATCH', 'Run macfan helper install to update the helper.', 4,
                          helper_version=__version__, client_version=request['version'])
    return request


def send(connection, message):
    connection.sendall((json.dumps(message, allow_nan=False, separators=(',', ':')) + '\n').encode())


def worker_command(request):
    # Only this immutable installed executable may be launched by the service.
    if request['operation'] == 'set':
        return [str(EXECUTABLE), '--internal-guardian']
    arguments = [str(EXECUTABLE), 'fans', 'auto']
    for fan in request['fans']:
        arguments.extend(['--fan', str(fan)])
    if request['experimental_ftst']:
        arguments.append('--experimental-ftst')
    return arguments


def relay(connection, request, stopping, command=None, env=None):
    """A disconnected caller closes guardian stdin; the guardian owns rollback."""
    environment = env if env is not None else {
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/var/root',
        'LANG': 'en_US.UTF-8', 'PYINSTALLER_RESET_ENVIRONMENT': '1',
    }
    process = subprocess.Popen(command or worker_command(request), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True, cwd='/', env=environment)
    connected = True
    last = None
    buffer = b''
    try:
        if request['operation'] == 'set':
            config = {key: request[key] for key in ('fans', 'rpm', 'duration', 'experimental_ftst')}
            config['demo'] = False
            process.stdin.write((json.dumps(config) + '\n').encode())
            process.stdin.flush()
        else:
            process.stdin.close()
        while True:
            if stopping.is_set() and not process.stdin.closed:
                process.stdin.close()
            watched = [process.stdout] + ([connection] if connected else [])
            ready, _, _ = select.select(watched, [], [], .25)
            if connected and connection in ready:
                # EOF or any byte after the request cancels; never another command.
                try:
                    connection.recv(1)
                except OSError:
                    pass
                if not process.stdin.closed:
                    process.stdin.close()
                # Keep the write half open on graceful cancellation so the CLI
                # can still receive restoration confirmation.
                connected = False
            if process.stdout in ready:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    last = json.loads(line)
                    try:
                        send(connection, last)
                    except OSError:
                        connected = False
                        if not process.stdin.closed:
                            process.stdin.close()
            # Child output is limited to a handful of envelopes, never a log stream.
            if len(buffer) > 262144:
                raise RuntimeError('Oversized guardian response')
        code = process.wait()
        if request['operation'] == 'set' and (code < 0 or (code == 0 and
                (not last or last.get('event') != 'control_finished'))):
            code = 7
            send(connection, failure(MacFanError('RESTORE_UNCONFIRMED',
                 'Guardian exited without restoration confirmation. Run fans auto.', 7)))
        send(connection, {'helper_exit_code': max(code, 0) if code >= 0 else 7})
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        # Never kill the isolated guardian. EOF triggers its own bounded rollback.
        if process.poll() is not None:
            process.wait()


def handle(connection, allowed_uid, stopping):
    try:
        connection.settimeout(3)
        authorize(connection, allowed_uid)
        request = validate_request(read_request(connection))
        if request['operation'] == 'status':
            send(connection, success({'installed': True, 'running': True, 'version': __version__,
                                     'build': build_id(), 'authorized_uid': allowed_uid, 'protocol': PROTOCOL}))
            send(connection, {'helper_exit_code': 0})
        else:
            relay(connection, request, stopping)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        error = exc if isinstance(exc, MacFanError) else MacFanError('HELPER_IO_ERROR', str(exc))
        try:
            send(connection, failure(error))
            send(connection, {'helper_exit_code': error.exit_code})
        except OSError:
            pass
    finally:
        connection.close()


def check_root_path(path, directory=False):
    info = path.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
        raise MacFanError('UNSAFE_HELPER_PATH', 'Helper files must be root-owned and not writable by other users.', path=str(path))
    if directory and not stat.S_ISDIR(info.st_mode):
        raise MacFanError('UNSAFE_HELPER_PATH', 'Expected helper directory.', path=str(path))


def serve():
    if os.geteuid() != 0 or not getattr(sys, 'frozen', False) or Path(sys.executable).resolve() != EXECUTABLE:
        raise MacFanError('PERMISSION_DENIED', 'The helper must run from its installed root-owned bundle.', 4)
    for path in (BASE, APP, APP / 'Contents', APP / 'Contents/MacOS'):
        check_root_path(path, directory=True)
    check_root_path(EXECUTABLE)
    check_root_path(CONFIG)
    config = json.loads(CONFIG.read_text())
    allowed_uid = config['authorized_uid']
    if type(allowed_uid) is not int or allowed_uid < 501:
        raise invalid('Invalid helper owner.')
    SOCKET_DIRECTORY.mkdir(mode=0o755, exist_ok=True)
    check_root_path(SOCKET_DIRECTORY, directory=True)
    SOCKET.unlink(missing_ok=True)  # protected parent; stale socket after restart
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stopping = threading.Event()
    slots = threading.BoundedSemaphore(8)
    def stop(*_): stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    os.umask(0o077)
    server.bind(str(SOCKET))
    os.chown(SOCKET, allowed_uid, -1)
    os.chmod(SOCKET, 0o600)
    server.listen(8)
    server.settimeout(.5)
    def work(connection):
        try: handle(connection, allowed_uid, stopping)
        finally: slots.release()
    try:
        while not stopping.is_set():
            try: connection, _ = server.accept()
            except socket.timeout: continue
            if slots.acquire(blocking=False):
                threading.Thread(target=work, args=(connection,), daemon=True).start()
            else:
                connection.close()
    finally:
        server.close()
        # Exiting closes child pipes as well. Detached guardians remain alive to
        # restore; launchd KillMode is configured not to kill their process groups.
        SOCKET.unlink(missing_ok=True)
    return 0


def request(operation, *, output=emit, **parameters):
    payload = {'protocol': PROTOCOL, 'version': __version__, 'build': build_id(), 'operation': operation, **parameters}
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    interrupted = False
    last = None
    can_signal = threading.current_thread() is threading.main_thread()
    old = signal.getsignal(signal.SIGTERM) if can_signal else None
    def stop(*_): raise KeyboardInterrupt
    try:
        info = SOCKET_DIRECTORY.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
            raise MacFanError('UNSAFE_HELPER_PATH', 'Helper socket directory is not trusted.', 4)
        connection.settimeout(3)
        connection.connect(str(SOCKET))
        if peer_uid(connection) != 0:
            raise MacFanError('HELPER_ACCESS_DENIED', 'The local service is not running as root.', 4)
        send(connection, payload)
        connection.settimeout(None)
        if can_signal: signal.signal(signal.SIGTERM, stop)
        raw = b''
        while True:
            try:
                chunk = connection.recv(65536)
            except KeyboardInterrupt:
                if interrupted:
                    raise
                interrupted = True
                connection.shutdown(socket.SHUT_WR)
                continue
            if not chunk:
                raise MacFanError('RESTORE_UNCONFIRMED' if operation == 'set' else 'HELPER_UNAVAILABLE',
                                  'Helper disconnected before completing the request.', 7 if operation == 'set' else 5)
            raw += chunk
            if len(raw) > 262144:
                raise invalid('Oversized helper response.')
            while b'\n' in raw:
                line, raw = raw.split(b'\n', 1)
                message = json.loads(line)
                if 'helper_exit_code' in message:
                    code = message['helper_exit_code']
                    if code == 0 and operation == 'set' and (not last or last.get('event') != 'control_finished'):
                        raise MacFanError('RESTORE_UNCONFIRMED', 'No restoration confirmation received.', 7)
                    return 130 if interrupted and code == 0 else code
                last = message
                output(message)
    except (FileNotFoundError, ConnectionRefusedError):
        raise MacFanError('HELPER_NOT_INSTALLED', 'Run macfan helper install once to authorize fan control.', 4) from None
    except PermissionError:
        raise MacFanError('HELPER_ACCESS_DENIED', 'This user cannot access the installed helper.', 4) from None
    finally:
        if can_signal: signal.signal(signal.SIGTERM, old)
        connection.close()


def status():
    messages = []
    try:
        code = request('status', output=messages.append)
        if code or not messages[-1].get('ok'):
            return {'installed': True, 'running': False, 'error': messages[-1].get('error')}
        data = messages[-1]['data']
        return {**data, 'compatible': data['version'] == __version__ and data['build'] == build_id()}
    except (MacFanError, OSError) as exc:
        return {'installed': PLIST.exists(), 'running': False,
                'error': exc.as_dict() if isinstance(exc, MacFanError) else {'message': str(exc)}}
