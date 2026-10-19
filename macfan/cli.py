from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

from . import __version__
from .errors import MacFanError, invalid
from .guardian import validate_duration
from .hardware import native_hardware
from .manifest import MANIFEST
from .launch import guardian_command
from .protocol import emit, failure, success
from .runtime import lock, runtime_directory
from .temperatures import sensor_report


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise invalid(message)


def parser():
    root = Parser(description="Local JSON-first Mac sensor monitoring and bounded fan control. Global options precede commands.",
                  epilog="Launch the desktop window separately with --gui (optionally --demo).")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--demo", action="store_true", help="use explicit synthetic hardware")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the machine-readable command contract")
    commands.add_parser("capabilities", help="inspect available hardware interfaces")
    commands.add_parser("doctor", help="produce read-only installation and hardware diagnostics")
    helper = commands.add_parser("helper", help="install, inspect or remove the local privileged helper")
    helper.add_argument("helper_action", choices=("install", "status", "uninstall"))
    agent = commands.add_parser("agent", help="install the user-local CLI and Agent skill")
    integration = agent.add_subparsers(dest="agent_action", required=True)
    install = integration.add_parser("install", help="configure this bundled app for Codex or Claude Code")
    install.add_argument("--target", choices=("codex", "claude", "both"), default="codex")
    install.add_argument("--user-root", type=Path, default=Path.home(), help="user directory (defaults to the current home)")
    sensors = commands.add_parser("sensors", help="read raw temperature sensors")
    sensors.add_argument("--source", choices=("all", "smc", "iohid"), default="all")
    from .sensor_labels import GROUPS
    sensors.add_argument("--group", choices=("all", *GROUPS), default="all", help="filter identified component groups")
    commands.add_parser("snapshot", help="read temperatures and fan state")
    watch = commands.add_parser("watch", help="stream snapshots as NDJSON")
    watch.add_argument("--interval", type=float, default=1, help="seconds between samples (0.2..3600)")
    watch.add_argument("--count", type=int, help="stop after this many samples")
    fans = commands.add_parser("fans", help="inspect or control fans")
    actions = fans.add_subparsers(dest="action")
    actions.add_parser("list", help="read fan state (default)")
    set_fan = actions.add_parser("set", help="hold fixed RPM for a bounded foreground session, then restore auto")
    auto = actions.add_parser("auto", help="restore automatic control, including during a live session")
    for command in (set_fan, auto):
        command.add_argument("--fan", action="append", required=True, help="zero-based ID; repeat for several fans, or use all")
        command.add_argument("--experimental-ftst", action="store_true", help="explicitly enable unverified global Ftst protocol")
    set_fan.add_argument("--rpm", type=int, required=True)
    set_fan.add_argument("--duration", type=float, required=True, help="session duration in seconds (1..3600)")
    rules = commands.add_parser("rules", help="inspect, validate, edit and run local temperature rules")
    rule_actions = rules.add_subparsers(dest="rules_action", required=True)
    for name in ("show", "validate", "apply", "enable", "disable", "status", "run"):
        command = rule_actions.add_parser(name)
        command.add_argument("--config", type=Path, help="rules destination (default: MACFAN_RULES_PATH or ~/Library/Application Support/MacFan/rules.json)")
        if name in ("apply", "enable", "disable"):
            command.add_argument("--wait", type=float, default=0, help="wait up to 10 seconds for the runner to load this exact generation")
            command.add_argument("--if-revision", help="reject instead of overwriting an externally edited revision")
        if name in ("validate", "apply"):
            source = command.add_mutually_exclusive_group(required=name == "apply")
            source.add_argument("--file", type=Path, help="input JSON file; --config remains the destination")
            source.add_argument("--stdin", action="store_true", help="read input JSON from stdin")
        if name == "run":
            command.add_argument("--parent-watch", action="store_true", help="stop and restore owned control when stdin closes")
            command.add_argument("--count", type=int, help="stop after this many one-second samples, then restore")
    return root


def select_fans(selection, count):
    if selection == ["all"]:
        indices = list(range(count))
    else:
        try:
            indices = [int(value) for value in selection]
        except ValueError:
            raise invalid("Use integer fan IDs or --fan all.") from None
    if not indices or len(set(indices)) != len(indices) or any(i < 0 or i >= count for i in indices):
        raise invalid("Select existing, distinct fan IDs; this machine may have no fans.", fan_count=count)
    return indices


def snapshot(hw):
    sensors = hw.temperatures()
    fans = hw.fans()
    report = sensor_report(sensors, hw.warnings)
    report.update(fans=fans, partial=report["partial"] or any(row["errors"] for row in fans))
    return report


def control_process(config):
    process = subprocess.Popen(guardian_command(), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, start_new_session=True)
    interrupted = False
    last_message = None
    old_handler = signal.getsignal(signal.SIGTERM)

    def forward(line):
        nonlocal last_message
        last_message = json.loads(line)
        sys.stdout.write(line)
        sys.stdout.flush()

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        process.stdin.write(json.dumps(config) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            forward(line)
    except KeyboardInterrupt:
        interrupted = True
        process.stdin.close()
        # Continue consuming the restoration result. A second Ctrl-C may leave
        # the isolated guardian running, with stdin closed and its own deadline.
        for line in process.stdout:
            forward(line)
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        # Never terminate the guardian on a parent timeout: that would suppress
        # restoration. It observes EOF even if this process is killed.
    result = process.wait()
    if result < 0 or (not interrupted and (not last_message or
            (last_message.get("ok") and last_message.get("event") != "control_finished"))):
        emit(failure(MacFanError("RESTORE_UNCONFIRMED", "The control process exited without a verified restoration result. Run fans auto and inspect the hardware.",
                                7, guardian_exit_status=result), config["demo"]))
        return 7
    return 130 if interrupted and result == 0 else result


def main(argv=None):
    hw = None
    args = None
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser().parse_args(raw_args)
        if args.command == "schema":
            emit(success(MANIFEST, args.demo))
            return 0
        if args.command == "doctor":
            from .doctor import diagnose
            emit(success(diagnose(args.demo), args.demo))
            return 0
        if args.command == "helper":
            if args.demo:
                raise invalid("Helper management cannot be combined with --demo.")
            if args.helper_action == "status":
                from .helper import status
                emit(success(status()))
            else:
                from .helper_install import manage
                emit(success(manage(args.helper_action)))
            return 0
        if args.command == "agent":
            if args.demo:
                raise invalid("Agent installation cannot be combined with --demo.")
            if os.geteuid() == 0:
                raise invalid("Install Agent integration as the current user, without sudo.")
            if not getattr(sys, "frozen", False):
                raise invalid("Run agent install using the executable inside the installed MacFan.app.")
            from .agent import integrate
            emit(success(integrate(Path(sys.executable), args.user_root, args.target)))
            return 0
        if args.command == "rules":
            from . import rules
            path = (args.config.expanduser().absolute() if args.config else rules.default_path(args.demo))
            action = args.rules_action
            if action in ("apply", "enable", "disable") and (not math.isfinite(args.wait) or not 0 <= args.wait <= 10):
                raise invalid("--wait must be finite and between 0 and 10 seconds.")
            if action == "show":
                document = rules.load(path)
                emit(success({"path": str(path), "config": document.config, "revision": document.revision,
                              "generation": document.generation, "exists": document.exists, "runtime": rules.status(path)}, args.demo))
                return 0
            if action == "status":
                emit(success(rules.status(path), args.demo))
                return 0
            if action in ("validate", "apply"):
                if args.stdin:
                    raw = sys.stdin.buffer.read(rules.MAX_BYTES + 1)
                    config = rules.decode(raw)
                elif args.file:
                    with args.file.open("rb") as stream:
                        config = rules.decode(stream.read(rules.MAX_BYTES + 1))
                else:
                    config = rules.load(path).config
                if action == "validate":
                    emit(success({"valid": True, "config": config, "path": str(path)}, args.demo))
                else:
                    document = rules.save(path, config, args.if_revision)
                    emit(success(rules.saved_result(document, args.wait), args.demo))
                return 0
            if action in ("enable", "disable"):
                document = rules.load(path)
                document.config["enabled"] = action == "enable"
                # Protect the read/modify/write even if no explicit CAS was requested.
                document = rules.save(path, document.config, args.if_revision or document.revision)
                emit(success(rules.saved_result(document, args.wait), args.demo))
                return 0
            if args.count is not None and args.count < 1:
                raise invalid("Count must be positive.")
            if args.demo:
                from .demo import demo_hardware
                hw = demo_hardware()
            else:
                hw = native_hardware()
            return rules.run(path, hw, snapshot, emit, demo=args.demo,
                             parent_watch=args.parent_watch, count=args.count)
        if args.command == "watch":
            if not math.isfinite(args.interval) or not 0.2 <= args.interval <= 3600:
                raise invalid("Watch interval must be finite and between 0.2 and 3600 seconds.")
            if args.count is not None and args.count < 1:
                raise invalid("Count must be positive.")
        if args.command == "fans" and args.action == "set":
            validate_duration(args.duration)
        if args.demo:
            from .demo import demo_hardware
            hw = demo_hardware()
        else:
            hw = native_hardware(include_temperatures=args.command != "fans")
        if args.command == "fans":
            if args.action in ("set", "auto"):
                indices = select_fans(args.fan, hw.count())
                if not args.demo and os.geteuid() != 0:
                    hw.close()
                    hw = None
                    from .helper import request
                    parameters = {"fans": indices, "experimental_ftst": args.experimental_ftst}
                    if args.action == "set":
                        parameters.update(rpm=args.rpm, duration=args.duration)
                    return request(args.action, **parameters)
                if args.action == "set":
                    hw.close()
                    hw = None
                    return control_process({"fans": indices, "rpm": args.rpm, "duration": args.duration,
                                            "demo": args.demo, "experimental_ftst": args.experimental_ftst})
                with lock(runtime_directory(args.demo), "write.lock"):
                    hw.restore(indices, args.experimental_ftst)
                emit(success({"fans": indices, "automatic_mode_verified": True}, args.demo))
            else:
                emit(success({"fans": hw.fans(), "warnings": hw.warnings}, args.demo))
        elif args.command == "sensors":
            sensors = [row for row in hw.temperatures() if args.source in ("all", row["source"])
                       and args.group in ("all", row.get("group", "unknown"))]
            if not sensors:
                raise MacFanError("NO_TEMPERATURE_SENSORS", "No temperature sensors matched the requested source and component group.", 3, warnings=hw.warnings)
            emit(success(sensor_report(sensors, hw.warnings), args.demo))
        elif args.command == "snapshot":
            emit(success(snapshot(hw), args.demo))
        elif args.command == "capabilities":
            sensors = hw.temperatures()
            fans = hw.fans()
            model = None
            if sys.platform == "darwin" and not args.demo:
                result = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.model"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    model = result.stdout.strip()
            temperatures = sensor_report(sensors, hw.warnings)
            temperatures.pop("sensors")
            temperatures["partial"] = temperatures["partial"] or any(row["errors"] for row in fans)
            emit(success({**temperatures, "platform": sys.platform, "architecture": platform.machine(),
                          "os_version": platform.mac_ver()[0] if sys.platform == "darwin" else platform.release(),
                          "model": model, "sensor_count": len(sensors), "fan_count": len(fans),
                          "temperature_sources": sorted({row["source"] for row in sensors}),
                          "fans": fans, "write_requires_root": False,
                          "hardware_write_requires_privilege": not args.demo,
                          "requires_helper": not args.demo and os.geteuid() != 0,
                          "running_as_root": os.geteuid() == 0,
                          "experimental_ftst_present": hw.optional_info("Ftst") is not None}, args.demo))
        elif args.command == "watch":
            count = 0
            while args.count is None or count < args.count:
                started = time.monotonic()
                emit(success(snapshot(hw), args.demo, "sample"))
                count += 1
                if args.count is None or count < args.count:
                    time.sleep(max(0, args.interval - (time.monotonic() - started)))
        return 0
    except MacFanError as exc:
        emit(failure(exc, args.demo if args else "--demo" in raw_args))
        return exc.exit_code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # The control subprocess sees EOF and restores independently.
        sys.stdout = open(os.devnull, "w")
        return 0
    except (OSError, ValueError) as exc:
        emit(failure(MacFanError("IO_ERROR", str(exc)), args.demo if args else False))
        return 5
    finally:
        if hw:
            hw.close()
