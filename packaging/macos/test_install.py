#!/usr/bin/env python3
"""Exercise the real installer on macOS, using only a temporary user directory."""
import argparse
import hashlib
import json
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
    args = parser.parse_args()
    install_source = ["--dmg", args.dmg.resolve()] if args.dmg else ["--run-id", args.run_id]
    with tempfile.TemporaryDirectory(prefix="macfan-install-test-") as temporary:
        work = Path(temporary)
        user = work / "user's directory with spaces"
        user.mkdir()
        (user / ".zprofile").write_text("# Preserve user settings\n")
        command = ["/bin/bash", ROOT / "scripts/install-macos.sh", *install_source, "--agent", "both", "--user-root", user]
        first = run(*command, timeout=180)
        messages = [json.loads(line) for line in first.stdout.splitlines()]
        assert len(messages) == 2 and all(message["ok"] for message in messages), first.stdout
        assert messages[0]["data"]["installed"] and not messages[0]["data"]["hardware_settings_changed"]
        assert not messages[1]["data"]["hardware_write_tested"]
        print("Native diagnostic status:", messages[1]["data"]["status"])
        executable = user / "Applications/MacFan.app/Contents/MacOS/macfan"
        info = plistlib.loads((user / "Applications/MacFan.app/Contents/Info.plist").read_bytes())
        assert info["CFBundleExecutable"] == "MacFanUI", "Finder must launch the actual native app"
        assert (executable.parent / "MacFanUI").is_file()
        binary = user / ".local/bin/macfan"
        assert binary.resolve() == executable.resolve()
        for folder in (".agents", ".claude"):
            skill = user / folder / "skills/macfan/SKILL.md"
            assert skill.is_file() and "schema_version: 2" in skill.read_text()
        profile = (user / ".zprofile").read_text()
        assert profile.startswith("# Preserve user settings\n")
        assert (user / ".zprofile.before-macfan").read_text() == "# Preserve user settings\n"
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", user / "Applications/MacFan.app")
        run(*command, timeout=180)
        assert (user / ".zprofile").read_text() == profile
        assert not list((user / "Applications").glob("MacFan.previous.*.app"))
        demo_env = dict(os.environ, MACFAN_DEMO_DIR=str(work / "demo"))
        demo = json.loads(run(binary, "--demo", "doctor", env=demo_env).stdout)
        assert demo["simulated"] and demo["data"]["monitoring_available"]
        assert demo["schema_version"] == 2 and demo["data"]["status"] == "limited"
        assert demo["data"]["valid_sensor_count"] == 3 and demo["data"]["sensor_summary"]["unverified"] == 1
        controlled = run(binary, "--demo", "fans", "set", "--fan", "all", "--rpm", "3000", "--duration", "1", env=demo_env)
        assert json.loads(controlled.stdout.splitlines()[-1])["data"]["automatic_mode_verified"]
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
        print("PASS: app installation, both skills, CLI, profile preservation, repeated installation, and simulated control")


if __name__ == "__main__":
    main()
