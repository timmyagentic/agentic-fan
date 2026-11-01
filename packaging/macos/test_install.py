#!/usr/bin/env python3
"""Exercise the real installer on macOS, using only a temporary user directory."""
import argparse
import hashlib
import json
import runpy
import os
import plistlib
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(*arguments, **kwargs):
    result = subprocess.run([str(arg) for arg in arguments], capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Installer check failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dmg", type=Path)
    source.add_argument("--run-id")
    source.add_argument("--release", action="store_true", help="Download the public stable Release without gh")
    parser.add_argument("--previous-dmg", type=Path, help="Exercise replacement of an existing App/CLI/skill in the temporary user root")
    args = parser.parse_args()
    install_source = ["--dmg", args.dmg.resolve()] if args.dmg else ["--run-id", args.run_id] if args.run_id else []
    with tempfile.TemporaryDirectory(prefix="agenticfan-install-test-") as temporary:
        work = Path(temporary)
        user = work / "user's directory with spaces"
        user.mkdir()
        (user / ".zprofile").write_text("# Preserve user settings\n")
        command = ["/bin/bash", ROOT / "scripts/install-macos.sh", *install_source, "--agent", "both", "--user-root", user]
        # Existing user data (including a custom Hook) must survive both installs.
        settings = user / 'Library/Application Support/AgenticFan'
        hooks = settings / 'hooks'; hooks.mkdir(parents=True)
        original = {settings / 'rules.json': b'{"version":1,"enabled":false,"fan":0,"rules":[{"id":"retained","enabled":true,"sensor_group":"cpu","trigger_celsius":99,"release_celsius":95,"release_seconds":5,"target_rpm":"max"}]}',
                    hooks / 'custom.sh': b'#!/bin/sh\n# preserved\n'}
        for path, content in original.items(): path.write_bytes(content)
        from test_service_vm import hosted_mac, exercise, previous_release, seed_published_app, legacy_app_for_vm
        hosted_fixture = args.dmg and hosted_mac() and not args.previous_dmg
        previous_dmg = args.previous_dmg or (previous_release(work) if hosted_fixture else None)
        if hosted_fixture:
            old_digest = seed_published_app(previous_dmg, work, user)
        elif previous_dmg:
            run("/bin/bash", ROOT / "scripts/install-macos.sh", "--dmg", previous_dmg.resolve(),
                "--agent", "both", "--user-root", user, timeout=180)
            previous_version = run(user / ".local/bin/agenticfan", "--version").stdout.strip()
            assert previous_version == "0.4.5"
        first = run(*command, timeout=180)
        messages = [json.loads(line) for line in first.stdout.splitlines()]
        assert len(messages) == 2 and all(message["ok"] for message in messages), first.stdout
        assert messages[0]["data"]["installed"] and not messages[0]["data"]["hardware_settings_changed"]
        assert not messages[1]["data"]["hardware_write_tested"]
        print("Native diagnostic status:", messages[1]["data"]["status"])
        executable = user / "Applications/AgenticFan.app/Contents/MacOS/agenticfan"
        info = plistlib.loads((user / "Applications/AgenticFan.app/Contents/Info.plist").read_bytes())
        assert info["CFBundleExecutable"] == "AgenticFanUI", "Finder must launch the actual native app"
        assert info.get("LSUIElement") is True, "Installed app must remain a menu-bar accessory without a Dock icon"
        assert (executable.parent / "AgenticFanUI").is_file()
        assert (user / "Applications/AgenticFan.app/Contents/Resources/LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes(), "Installed app must retain the project license"
        binary = user / ".local/bin/agenticfan"
        assert binary.resolve() == executable.resolve()
        for folder in (".agents", ".claude"):
            skill = user / folder / "skills/agenticfan/SKILL.md"
            assert skill.is_file() and "schema_version: 2" in skill.read_text()
        profile = (user / ".zprofile").read_text()
        assert profile.startswith("# Preserve user settings\n")
        assert (user / ".zprofile.before-agenticfan").read_text() == "# Preserve user settings\n"
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", user / "Applications/AgenticFan.app")
        backups = list((user / "Applications").glob("AgenticFan.previous.*.app"))
        assert len(backups) == (1 if previous_dmg else 0)
        if previous_dmg:
            previous_cli = backups[0] / "Contents/MacOS/agenticfan"
            if hosted_fixture:
                assert hashlib.sha256(previous_cli.read_bytes()).digest() == old_digest
            else:
                assert run(previous_cli, "--version").stdout.strip() == previous_version
            assert run(binary, "--version").stdout.strip() == info["CFBundleShortVersionString"]
            for folder in (".agents", ".claude"):
                marker = user / folder / "skills/agenticfan/installation.json"
                assert json.loads(marker.read_text())["version"] == info["CFBundleShortVersionString"]
            print("PASS: published 0.4.5 app replaced; original binary retained; new CLI and both skills installed"
                  if hosted_fixture else "PASS: actual 0.4.5 app, CLI and both skills upgraded in place")
        run(*command, timeout=180)
        assert (user / ".zprofile").read_text() == profile
        assert list((user / "Applications").glob("AgenticFan.previous.*.app")) == backups
        for path, content in original.items():
            assert path.read_bytes() == content, 'Existing configuration changed during installation'
        if 'AgenticFanServiceBuild' in info:
            daemon = 'io.github.timmyagentic.agenticfan.control.plist'
            definition = plistlib.loads((executable.parents[1] / 'Library/LaunchDaemons' / daemon).read_bytes())
            assert definition['UserName'] == 'root' and definition['AbandonProcessGroup']
            assert definition['BundleProgram'] == 'Contents/MacOS/AgenticFanService' and 'Program' not in definition
            run(executable.parent / 'AgenticFanService', '--verify-worker')
            assert hashlib.sha256((executable.parent / 'AgenticFanControlWorker').read_bytes()).hexdigest() == info['AgenticFanServiceBuild']
            # A modified worker must fail before any privileged execution.
            pair = work / 'tamper'; pair.mkdir()
            import shutil
            shutil.copy2(executable.parent / 'AgenticFanService', pair / 'AgenticFanService')
            (pair / 'AgenticFanControlWorker').write_bytes(b'untrusted')
            assert subprocess.run([str(pair/'AgenticFanService'), '--verify-worker']).returncode != 0
            (pair / 'AgenticFanControlWorker').unlink()
            (pair / 'AgenticFanControlWorker').symlink_to(executable.parent / 'AgenticFanControlWorker')
            assert subprocess.run([str(pair/'AgenticFanService'), '--verify-worker']).returncode != 0
            if hosted_mac() and args.dmg:
                exercise(executable.parents[2], legacy_app_for_vm(work, backups[0]))
        demo_env = dict(os.environ, AGENTICFAN_DEMO_DIR=str(work / "demo"))
        demo = json.loads(run(binary, "--demo", "doctor", env=demo_env).stdout)
        assert demo["simulated"] and demo["data"]["monitoring_available"]
        assert demo["schema_version"] == 2 and demo["data"]["status"] == "limited"
        assert demo["data"]["valid_sensor_count"] == 3 and demo["data"]["sensor_summary"]["unverified"] == 1
        controlled = run(binary, "--demo", "fans", "set", "--fan", "all", "--rpm", "3000", "--duration", "1", env=demo_env)
        assert json.loads(controlled.stdout.splitlines()[-1])["data"]["automatic_mode_verified"]
        runpy.run_path(str(ROOT / 'packaging/macos/build.py'))['hook_smoke'](binary, work, demo_env)
        runpy.run_path(str(ROOT / 'packaging/macos/build.py'))['curve_smoke'](binary, work, demo_env)
        if args.dmg:
            corrupt = work / "corrupt.dmg"
            corrupt.write_bytes(b"not the downloaded disk image")
            expected = hashlib.sha256(args.dmg.read_bytes()).hexdigest()
            corrupt.with_suffix(".dmg.sha256").write_text(f"{expected}  corrupt.dmg\n")
            untouched = work / "must-not-be-created"
            failed = subprocess.run(["/bin/bash", str(ROOT / "scripts/install-macos.sh"), "--dmg", str(corrupt),
                                     "--user-root", str(untouched)], capture_output=True, text=True)
            assert failed.returncode != 0 and "checksum mismatch" in failed.stderr
            assert not untouched.exists()
        print("PASS: app installation, both skills, CLI, Hook and curve workflows, profile preservation, repeated installation, and simulated control")


if __name__ == "__main__":
    main()
