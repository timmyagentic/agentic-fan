#!/usr/bin/env python3
"""Replay simulated 255-sensor NDJSON through the real native Command transport.

macOS only. All sources, binaries, fixture data, caches and results go under the
required --output-dir. --baseline-ref snapshots an immutable Git Command.swift
for an apples-to-apples comparison; its failing memory budget is reported as
evidence, while the candidate still must pass. No helper or hardware is used.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def synthetic_snapshot() -> dict:
    groups = ("cpu", "gpu", "memory", "storage", "wireless", "power")
    sensors = []
    for index in range(255):
        group = groups[index % len(groups)]
        status = "invalid" if index % 29 == 0 else "unverified" if index % 31 == 0 else "ok"
        value = 45.0 + index % 55 + (index % 10) / 10
        sensors.append({"id": f"smc:T{index:03d}", "name": f"T{index:03d}",
                        "display_name": f"{group.upper()} temperature point {index + 1}",
                        "group": group, "group_name": group.upper(), "source": "smc",
                        "status": status, "value": value if status == "ok" else None,
                        "raw_value": value if status != "unverified" else 0.0,
                        "label_note": "Synthetic replay fixture; location is illustrative.",
                        "label_source": "synthetic_fixture", "label_status": "unverified"})
    overview = [{"group": group, "name": group.upper(), "value": max(
        sensor["value"] for sensor in sensors if sensor["group"] == group and sensor["status"] == "ok"),
                 "sensor_count": sum(sensor["group"] == group for sensor in sensors),
                 "valid_count": sum(sensor["group"] == group and sensor["status"] == "ok" for sensor in sensors)}
                for group in groups]
    return {"ok": True, "schema_version": 2, "simulated": True,
            "data": {"sensors": sensors, "temperature_overview": overview,
                     "fans": [{"id": 0, "name": "Fan 1", "actual_rpm": 3000.0,
                               "target_rpm": 3000.0, "min_rpm": 1000.0, "max_rpm": 5500.0,
                               "mode": "auto", "control_available": True}]}}


def fixture(source: Path | None) -> dict:
    document = json.loads(source.read_text()) if source else synthetic_snapshot()
    document = copy.deepcopy(document)
    if "data" not in document:
        document = {"ok": True, "schema_version": 2, "data": document}
    if len(document.get("data", {}).get("sensors", [])) != 255:
        raise ValueError("The memory fixture must contain exactly 255 sensors")
    document.update(ok=True, simulated=True, event="sample")
    return document


def produce(args: argparse.Namespace) -> None:
    sample = fixture(args.snapshot)
    continuation = args.continue_path

    def emit(event: str, data: dict | None = None, sequence: int | None = None) -> None:
        message = sample if event == "sample" else {"ok": True, "schema_version": 2,
                                                     "simulated": True, "event": event, "data": data or {}}
        message["emitted_uptime_ns"] = time.monotonic_ns()
        if sequence is not None:
            message["test_sequence"] = sequence
        sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    for sequence in range(1, args.warmup + 1):
        emit("sample", sequence=sequence)
    emit("memory_checkpoint", {"phase": "warmup", "expected_sequence": args.warmup})
    deadline = time.monotonic() + 60
    while not continuation.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Native consumer did not release the warmup checkpoint")
        time.sleep(0.005)
    controls = {
        args.warmup + args.frames // 4: ("control_started", {"target_rpm": 4900,
            "expires_at": "2026-10-19T00:00:00.000000+00:00"}),
        args.warmup + args.frames // 2: ("control_confirmed", {}),
        args.warmup + args.frames * 3 // 4: ("control_finished", {"automatic_mode_verified": True}),
    }
    final = args.warmup + args.frames
    for sequence in range(args.warmup + 1, final + 1):
        emit("sample", sequence=sequence)
        if sequence in controls:
            event, data = controls[sequence]
            emit(event, {**data, "after_sequence": sequence})
    emit("memory_barrier", {"expected_sequence": final})
    emit("memory_checkpoint", {"phase": "after_replay", "expected_sequence": final})
    # The reader's long-lived dispatch closure remains blocked in read() until
    # the native probe finishes measuring and closes its parent-watch stdin.
    sys.stdin.read()
    emit("memory_producer_finished", {"produced_sample_frames": final})


def run_one(args: argparse.Namespace, output: Path, label: str, command: bytes,
            snapshot_path: Path, environment: dict[str, str]) -> dict:
    directory = output / label
    sources = directory / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    command_path = sources / "Command.swift"
    command_path.write_bytes(command)
    for filename in ("Models.swift", "MemoryTests.swift"):
        shutil.copyfile(ROOT / "native" / filename, sources / filename)
    producer_path = sources / "test-native-memory.py"
    shutil.copyfile(Path(__file__), producer_path)
    binary = directory / "memory-probe"
    build = ["xcrun", "swiftc", "-O", "-swift-version", "5", "-target", f"{platform.machine()}-apple-macos15.0",
             "-module-cache-path", environment["SWIFT_MODULECACHE_PATH"],
             str(sources / "Models.swift"), str(command_path), str(sources / "MemoryTests.swift"),
             "-o", str(binary)]
    built = subprocess.run(build, capture_output=True, text=True, env=environment)
    (directory / "build.stdout.txt").write_text(built.stdout)
    (directory / "build.stderr.txt").write_text(built.stderr)
    if built.returncode:
        raise RuntimeError(f"Native memory harness failed to compile; see {directory / 'build.stderr.txt'}")
    continuation = directory / "continue-after-warmup"
    continuation.unlink(missing_ok=True)
    invocation = [str(binary), "--backend", sys.executable, "--producer-script", str(producer_path),
                  "--snapshot", str(snapshot_path), "--continue-path", str(continuation),
                  "--warmup", str(args.warmup), "--frames", str(args.frames),
                  "--sleep-ms", str(args.sleep_ms), "--limit-mib", str(args.limit_mib),
                  "--max-footprint-mib", str(args.max_footprint_mib),
                  "--timeout", str(args.timeout), "--label", label]
    completed = subprocess.run(invocation, capture_output=True, text=True, env=environment,
                               timeout=args.timeout + 15)
    (directory / "probe.stdout.json").write_text(completed.stdout)
    (directory / "probe.stderr.txt").write_text(completed.stderr)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Native probe did not return JSON; see {directory}") from error
    result["command_source_sha256"] = hashlib.sha256(command).hexdigest()
    result["probe_return_code"] = completed.returncode
    result["build_command"] = build
    result["probe_command"] = invocation
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--snapshot", type=Path, help="Read-only captured snapshot; otherwise use a synthetic 255-sensor fixture")
    parser.add_argument("--continue-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", type=Path, help="Required artifact directory for compilation and results")
    parser.add_argument("--command-source", type=Path, default=ROOT / "native" / "Command.swift")
    parser.add_argument("--baseline-ref", help="Also replay Command.swift from an immutable Git revision")
    parser.add_argument("--label", default="candidate")
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--frames", type=int, default=5000)
    parser.add_argument("--sleep-ms", type=float, default=2)
    parser.add_argument("--limit-mib", type=float, default=64)
    parser.add_argument("--max-footprint-mib", type=float, default=1280,
                        help="Stop replay before a regression can consume unbounded RAM")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.producer:
        if not args.snapshot or not args.continue_path:
            parser.error("Producer requires --snapshot and --continue-path")
        produce(args)
        return 0
    if sys.platform != "darwin":
        parser.error("The native physical-footprint harness requires macOS")
    if platform.machine() not in ("arm64", "x86_64"):
        parser.error("The native physical-footprint harness requires arm64 or x86_64 macOS")
    if not args.output_dir:
        parser.error("--output-dir is required")
    if (args.warmup <= 0 or args.frames < 4 or args.sleep_ms < 0 or args.limit_mib <= 0
            or args.max_footprint_mib <= args.limit_mib or args.timeout <= 0
            or not all(math.isfinite(value) for value in (args.sleep_ms, args.limit_mib,
                                                        args.max_footprint_mib, args.timeout))):
        parser.error("Replay counts and budgets must be positive")
    if Path(args.label).name != args.label or args.label in (".", "..", "baseline"):
        parser.error("--label must be a simple directory name other than baseline")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for variable, name in (("TMPDIR", "tmp"), ("CLANG_MODULE_CACHE_PATH", "clang-cache"),
                           ("SWIFT_MODULECACHE_PATH", "swift-cache")):
        directory = output / name
        directory.mkdir(exist_ok=True)
        environment[variable] = str(directory)
    snapshot_path = output / "replay-snapshot.json"
    snapshot_path.write_text(json.dumps(fixture(args.snapshot), ensure_ascii=False, indent=2) + "\n")
    # Freeze candidate bytes before a potentially slow baseline replay; a
    # concurrent editor cannot change the version compiled for this comparison.
    candidate = args.command_source.read_bytes()
    (output / "candidate-command.swift").write_bytes(candidate)
    results = []
    if args.baseline_ref:
        baseline = subprocess.run(["git", "show", f"{args.baseline_ref}:native/Command.swift"],
                                  cwd=ROOT, capture_output=True, check=True).stdout
        results.append(run_one(args, output, "baseline", baseline, snapshot_path, environment))
    results.append(run_one(args, output, args.label, candidate, snapshot_path, environment))
    report = {"simulated": True, "hardware_writes": False, "fixture": str(snapshot_path),
              "fixture_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
              "baseline_ref": args.baseline_ref, "runs": results,
              "passed": results[-1].get("passed") is True and results[-1]["probe_return_code"] == 0}
    (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
