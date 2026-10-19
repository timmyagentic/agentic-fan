"""One bounded control session, isolated from the invoking CLI's process group.

The parent keeps stdin open. EOF, a stop byte, a signal, expiry, an observation
gap, or an I/O failure all lead through the same automatic-mode restoration.
"""
from __future__ import annotations

import json
import math
import os
import select
import signal
import sys
import time
from datetime import datetime, timezone

from .errors import MacFanError, invalid
from .hardware import Hardware, native_hardware
from .protocol import emit, failure, success
from .runtime import lock, runtime_directory


def validate_duration(duration):
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or not 1 <= duration <= 3600:
        raise invalid("Duration must be finite and between 1 and 3600 seconds.")


def run_session(hw: Hardware, directory, indices, rpm, duration, experimental_ftst, output,
                wait_stop, monotonic=time.monotonic, wall_clock=time.time):
    validate_duration(duration)
    if not isinstance(rpm, int) or isinstance(rpm, bool):
        raise invalid("RPM must be an integer.")
    if not isinstance(indices, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in indices):
        raise invalid("Fan IDs must be integers.")
    reason = "expired"
    confirmed = False
    primary_error = None
    armed = False
    with lock(directory, "session.lock", blocking=False):
        try:
            with lock(directory, "write.lock"):
                hw.validate_control(indices, rpm, experimental_ftst)
                if wait_stop(0):
                    return
                start = monotonic()
                start_wall = wall_clock()
                def wait_apply(delay):
                    elapsed = max(monotonic() - start, wall_clock() - start_wall)
                    if elapsed >= duration:
                        raise MacFanError("CONTROL_NOT_CONFIRMED", "Session expired while waiting for hardware readback.")
                    if wait_stop(min(delay, duration - elapsed)):
                        raise MacFanError("CONTROL_CANCELLED", "Control cancelled while waiting for hardware readback.", 130)
                armed = True
                hw.apply(indices, rpm, experimental_ftst, wait=wait_apply)
            last_mono, last_wall = monotonic(), wall_clock()
            next_sample = last_mono
            output(success({"fans": indices, "target_rpm": rpm, "duration_seconds": duration,
                            "expires_at": datetime.fromtimestamp(start_wall + duration, timezone.utc).isoformat(),
                            "guardian_pid": os.getpid(), "status": "target_written",
                            "experimental_ftst": experimental_ftst}, hw.simulated, "control_started"))
            while True:
                current, current_wall = monotonic(), wall_clock()
                elapsed = max(current - start, current_wall - start_wall)
                if current - last_mono > 3 or abs(current_wall - last_wall) > 3:
                    reason = "observation_gap"
                    break
                last_mono, last_wall = current, current_wall
                if current >= next_sample:
                    modes = [hw.mode(i) for i in indices]
                    if all(mode == "auto" for mode in modes):
                        reason = "external_auto"
                        break
                    if any(mode != "manual" for mode in modes):
                        raise MacFanError("CONTROL_LOST", "Selected fan modes changed during control.")
                    if any(abs(hw.number(f"F{i}Tg") - rpm) > 1 for i in indices):
                        raise MacFanError("CONTROL_LOST", "A target RPM changed during control; another controller may be active.")
                    actual = {str(i): hw.number(f"F{i}Ac") for i in indices}
                    if not confirmed and all(abs(value - rpm) <= max(100, rpm * 0.08) for value in actual.values()):
                        confirmed = True
                        output(success({"actual_rpm": actual, "target_rpm": rpm}, hw.simulated, "control_confirmed"))
                    next_sample = current + 1
                if not confirmed and elapsed >= min(15, duration):
                    raise MacFanError("CONTROL_NOT_CONFIRMED", "Actual RPM did not reach the target tolerance before the confirmation deadline.",
                                      target_rpm=rpm, tolerance_rpm=max(100, rpm * 0.08))
                if elapsed >= duration:
                    break
                if wait_stop(min(0.25, duration - elapsed)):
                    reason = "cancelled"
                    break
        except BaseException as exc:
            primary_error = exc
        finally:
            if armed:
                try:
                    with lock(directory, "write.lock"):
                        hw.restore(indices, experimental_ftst)
                except MacFanError as restore_error:
                    if primary_error:
                        restore_error.details["initial_error"] = str(primary_error)
                    primary_error = restore_error
        if primary_error:
            raise primary_error
        if armed:
            output(success({"fans": indices, "reason": reason, "automatic_mode_verified": True,
                            "target_observed": confirmed}, hw.simulated, "control_finished"))


def main():
    demo = False
    hw = None
    stopped = False
    lease_seconds = None
    last_heartbeat = time.monotonic()

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    def wait_stop(timeout):
        nonlocal last_heartbeat
        if stopped:
            return True
        if lease_seconds is not None and time.monotonic() - last_heartbeat >= lease_seconds:
            return True
        readable, _, _ = select.select([sys.stdin.fileno()], [], [], timeout)
        if readable:
            raw = os.read(sys.stdin.fileno(), 1024)
            if lease_seconds is not None and raw and all(byte == ord('.') for byte in raw):
                last_heartbeat = time.monotonic()
                return False
            return True  # Includes EOF after SIGKILL of the parent.
        return stopped

    def output(message):
        # Losing the consumer must not skip restoration.
        try:
            emit(message)
        except BrokenPipeError:
            pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        raw = bytearray()
        while len(raw) < 65536:
            byte = os.read(sys.stdin.fileno(), 1)
            if not byte or byte == b"\n":
                break
            raw.extend(byte)
        config = json.loads(raw)
        lease_seconds = config.get('lease_seconds')
        if lease_seconds is not None and (type(lease_seconds) not in (int, float)
                                         or not math.isfinite(lease_seconds) or not 1 <= lease_seconds <= 10):
            raise invalid('A heartbeat lease must be finite and between 1 and 10 seconds.')
        last_heartbeat = time.monotonic()
        demo = config.get("demo") is True
        if config.get("experimental_ftst") not in (True, False):
            raise invalid("experimental_ftst must be boolean.")
        if not demo and os.geteuid() != 0:
            raise MacFanError("PERMISSION_DENIED", "Fan control requires sudo.", 4)
        if demo:
            from .demo import demo_hardware
            hw = demo_hardware()
        else:
            hw = native_hardware(include_temperatures=False)
        run_session(hw, runtime_directory(demo), config["fans"], config["rpm"], config["duration"],
                    config["experimental_ftst"], output, wait_stop)
        return 0
    except MacFanError as exc:
        output(failure(exc, demo))
        return exc.exit_code
    except Exception as exc:
        output(failure(MacFanError("INTERNAL_ERROR", str(exc)), demo))
        return 5
    finally:
        if hw:
            hw.close()


if __name__ == "__main__":
    raise SystemExit(main())
