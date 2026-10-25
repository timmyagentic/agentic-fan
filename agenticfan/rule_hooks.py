"""Bounded user-process notifications; never part of the privileged control path."""
from collections import deque
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import threading

from .errors import invalid


def validate_hook(value):
    if value is None:
        return
    if (not isinstance(value, dict) or 'program' not in value
            or set(value) - {'program', 'timeout_seconds'}):
        raise invalid('hook must be null or contain program and optional timeout_seconds.')
    program = value['program']
    if not isinstance(program, str) or '\x00' in program or not Path(program).is_absolute():
        raise invalid('hook.program must be an absolute executable path, not a shell command.')
    timeout = value.get('timeout_seconds', 10)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not .1 <= timeout <= 60:
        raise invalid('hook.timeout_seconds must be finite, from 0.1 through 60 seconds.')


class HookDispatcher:
    """One worker and at most 16 queued events; hook output cannot fill memory/logs."""
    def __init__(self):
        self.condition = threading.Condition()
        self.pending = deque()
        self.worker = self.process = None
        self.closed = False
        self.last_result = None
        self.dropped = 0
        self.generation = 0

    def submit(self, hook, event):
        if hook is None:
            return
        with self.condition:
            if self.closed or len(self.pending) >= 16:
                self.dropped += 1
                return
            self.pending.append((deepcopy(hook), deepcopy(event), self.generation))
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, name='rule-hook', daemon=True)
                try:
                    self.worker.start()
                except RuntimeError:
                    self.worker = None
                    self.pending.clear()
                    self.last_result = {'event_id': event['event_id'], 'rule_id': event['rule']['id'],
                                        'status': 'failed', 'reason': 'worker_start_failed'}
            self.condition.notify()

    def status(self):
        with self.condition:
            return {'queued': len(self.pending), 'running': self.process is not None,
                    'dropped': self.dropped, 'last_result': deepcopy(self.last_result)}

    @staticmethod
    def _kill(process):
        # Also stop normal child processes (for example a lark-cli launched by a script).
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending)
                if self.closed:
                    return
                hook, event, generation = self.pending.popleft()
            result = {'event_id': event['event_id'], 'rule_id': event['rule']['id'], 'status': 'failed'}
            process = None
            try:
                if os.geteuid() == 0:
                    result['reason'] = 'root_execution_refused'
                else:
                    # A frozen Python runtime must not contaminate an external executable.
                    env = {key: value for key, value in os.environ.items()
                           if not key.startswith(('_PYI_', 'PYINSTALLER_'))
                           and key not in ('PYTHONHOME', 'PYTHONPATH')}
                    with self.condition:
                        if self.closed or generation != self.generation:
                            continue
                    # Process creation may itself be slow; never hold the sampling-side lock.
                    process = subprocess.Popen([hook['program']], stdin=subprocess.PIPE,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               cwd=str(Path(hook['program']).parent), env=env,
                                               start_new_session=True)
                    with self.condition:
                        self.process = process
                        revoked = self.closed or generation != self.generation
                    if revoked:
                        self._kill(process)
                    try:
                        process.communicate((json.dumps(event, allow_nan=False) + '\n').encode(),
                                            timeout=hook.get('timeout_seconds', 10))
                        result.update(status='succeeded' if process.returncode == 0 else 'failed',
                                      exit_code=process.returncode)
                    except subprocess.TimeoutExpired:
                        result['status'] = 'timed_out'
                    finally:
                        # Do not leave child processes behind even if the entry program exits early.
                        self._kill(process)
                        process.communicate()
            except OSError as exc:
                result['reason'] = type(exc).__name__
            finally:
                with self.condition:
                    if self.closed or generation != self.generation:
                        result['status'] = 'cancelled'
                    self.process = None
                    self.last_result = result

    def cancel(self):
        with self.condition:
            self.generation += 1
            self.dropped += len(self.pending)
            self.pending.clear()
            process = self.process
        if process is not None:
            self._kill(process)

    def close(self):
        with self.condition:
            self.closed = True
            self.dropped += len(self.pending)
            self.pending.clear()
            process, worker = self.process, self.worker
            self.condition.notify_all()
        if process is not None:
            self._kill(process)
        if worker is not None:
            worker.join(timeout=2)
