#!/usr/bin/env python3
"""Build and verify a real Apple Silicon app/DMG on macOS (never cross-compile)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def hook_smoke(executable: Path, directory: Path, env):
    with tempfile.TemporaryDirectory(prefix='hook-smoke-', dir=directory) as temporary:
        _hook_smoke(executable, Path(temporary), env)
    print('AGENTICFAN_HOOK_SMOKE_OK')


def _hook_smoke(executable: Path, directory: Path, env):
    config = directory / 'hook-check/rules.json'
    def command(action, *arguments, source=None):
        result = run(executable, '--demo', 'hook', action, '--config', config, *arguments,
                     input=source, cwd=directory, env=env, capture_output=True, text=True, timeout=15)
        message = json.loads(result.stdout)
        assert message['ok'] and message['simulated']
        return message['data']
    document = command('show')
    assert not config.parent.exists(), 'Hook discovery must not initialize files'
    command('validate', '--stdin', source=document['script']['content'])
    receipt = command('apply', '--stdin', '--if-revision', document['script']['revision'],
                      '--if-rules-revision', document['settings']['rules_revision'], source=document['script']['content'])
    assert receipt['script_saved'] and not receipt['document']['settings']['enabled']
    assert command('validate')['valid']
    state = command('status')
    assert state['script']['revision'] == receipt['script_saved_revision']
    assert not state['settings']['rules_enabled'] and 'content' not in state['script']
    # The frozen runtime must supply its own AST checker; this interpreter is never run.
    interpreter = directory / 'syntax-check/python3'
    interpreter.parent.mkdir()
    interpreter.write_text('#!/bin/sh\nexit 91\n'); interpreter.chmod(0o700)
    assert command('validate', '--stdin', source=f'#!{interpreter}\npass\n')['syntax']['status'] == 'passed'


def smoke(executable: Path, directory: Path, gui=True):
    """Exercise the bundled runtime outside the checkout, without system Python."""
    env = dict(os.environ, AGENTICFAN_DEMO_DIR=str(directory / "demo"),
               AGENTICFAN_RULES_PATH=str(directory / "demo/rules.json"), PATH="/usr/bin:/bin:/usr/sbin:/sbin")
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    for arguments in (
        ["schema"], ["--demo", "snapshot"],
        ["--demo", "fans", "set", "--fan", "all", "--rpm", "3000", "--duration", "1"],
    ):
        result = run(executable, *arguments, cwd=directory, env=env, capture_output=True, text=True, timeout=30)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        if not messages or not all(message["ok"] for message in messages):
            raise RuntimeError(f"Packaged CLI failed: {result.stdout}")
        if "set" in arguments and not messages[-1]["data"].get("automatic_mode_verified"):
            raise RuntimeError("Packaged guardian did not confirm restoration")
        if "--demo" in arguments and not all(message["simulated"] for message in messages):
            raise RuntimeError("Packaged smoke test was not explicitly simulated")
    hook_smoke(executable, directory, env)
    # Exercise rejection and presentation in the frozen app as well as source tests.
    # This file belongs exclusively to the explicitly simulated transport above.
    state_path = directory / "demo" / "state.json"
    state = json.loads(state_path.read_text())
    state["TCDX"] = struct.pack("<f", -9200.0).hex()
    state["Tg0D"] = struct.pack("<f", 115.6).hex()
    state_path.write_text(json.dumps(state))
    diagnostic = run(executable, "--demo", "doctor", cwd=directory, env=env, capture_output=True, text=True, timeout=30)
    message = json.loads(diagnostic.stdout)
    report = message["data"]
    if (message["schema_version"] != 2 or not message["simulated"] or report["status"] != "limited" or
            report["valid_sensor_count"] != 2 or report["sensor_summary"]["invalid"] != 1 or
            not any(row["raw_value"] == -9200 and row["value"] is None for row in report["sensor_issues"])):
        raise RuntimeError("Packaged app failed temperature-quality regression")
    if gui:
        for operation in ("status", "enable", "disable"):
            login = run(executable.parent / "AgenticFanUI", "--demo", "--login-item", operation,
                        cwd=directory, env=env, capture_output=True, text=True, timeout=30)
            report = json.loads(login.stdout)
            if not report["simulated"] or report["enabled"] or report["error"]:
                raise RuntimeError("Demo login item command was not isolated")
        run(executable, "--gui", "--demo", "--gui-smoke-test", cwd=directory, env=env, timeout=30)
        # Also exercise the exact native entry selected by LaunchServices/Finder.
        run(executable.parent / "AgenticFanUI", "--demo", "--smoke", cwd=directory, env=env, timeout=30)
        startup = run(executable.parent / "AgenticFanUI", "--demo", "--startup-smoke",
                      cwd=directory, env=env, capture_output=True, text=True, timeout=30)
        if "AGENTICFAN_STARTUP_SMOKE_OK" not in startup.stdout:
            raise RuntimeError(f"Native menu-bar startup failed: {startup.stdout}\n{startup.stderr}")


def notarize(path: Path, profile: str):
    run("xcrun", "notarytool", "submit", path, "--keychain-profile", profile, "--wait")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "macos")
    parser.add_argument("--identity", help="Installed Developer ID Application signing identity")
    parser.add_argument("--notary-profile", help="Existing notarytool keychain profile; requires --identity")
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or platform.machine() != "arm64":
        parser.error("DMG builds require an Apple Silicon Mac running macOS 15 or later; Linux cannot produce this artifact.")
    if int(platform.mac_ver()[0].split(".")[0]) < 15:
        parser.error("This build targets macOS 15 or later.")
    if args.notary_profile and not args.identity:
        parser.error("--notary-profile requires --identity")
    for tool in ("hdiutil", "codesign", "ditto", "xcrun"):
        if not shutil.which(tool):
            parser.error(f"Required macOS build tool is missing: {tool}")
    import PyInstaller

    version = runpy.run_path(str(ROOT / "agenticfan" / "__init__.py"))["__version__"]
    flavor = "" if args.notary_profile else "-signed-unnotarized" if args.identity else "-dev"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    dmg = output / f"AgenticFan-{version}-arm64{flavor}.dmg"
    if dmg.exists():
        parser.error(f"Output already exists; choose a clean --output directory: {dmg}")
    with tempfile.TemporaryDirectory(prefix="agenticfan-build-") as temporary:
        work = Path(temporary).resolve()
        from build_native import build as build_native
        native = build_native(work / "AgenticFanUI")
        products = work / "products"
        env = dict(os.environ)
        env["PYINSTALLER_CONFIG_DIR"] = str(work / "pyinstaller-cache")
        env.pop("AGENTICFAN_CODESIGN_IDENTITY", None)
        if args.identity:
            env["AGENTICFAN_CODESIGN_IDENTITY"] = args.identity
        run(sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
            "--distpath", products, "--workpath", work / "build", ROOT / "packaging/macos/agenticfan.spec",
            cwd=ROOT, env=env)
        app = products / "AgenticFan.app"
        executable = app / "Contents/MacOS/agenticfan"
        if not executable.is_file():
            raise RuntimeError("PyInstaller did not produce the expected application bundle")
        shutil.copy2(native, app / "Contents/MacOS/AgenticFanUI")
        (app / "Contents/Resources").mkdir(exist_ok=True)
        shutil.copy2(ROOT / "LICENSE", app / "Contents/Resources/LICENSE")
        # The OS must register the actual Cocoa executable, not an exec() trampoline.
        # Otherwise modern macOS rejects the status-item scene after Finder launch.
        info_path = app / "Contents/Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        info['CFBundleExecutable'] = 'AgenticFanUI'
        info_path.write_bytes(plistlib.dumps(info))
        signing = ["--options", "runtime", "--timestamp"] if args.identity else []
        run("codesign", "--force", "--sign", args.identity or "-", *signing, executable)
        run("codesign", "--force", "--sign", args.identity or "-", *signing, app / "Contents/MacOS/AgenticFanUI")
        run("codesign", "--force", "--sign", args.identity or "-", *signing, app)
        run("codesign", "--verify", "--deep", "--strict", app)
        smoke(executable, work)
        if args.notary_profile:
            archive = work / "AgenticFan.zip"
            run("ditto", "-c", "-k", "--keepParent", app, archive)
            notarize(archive, args.notary_profile)
            run("xcrun", "stapler", "staple", app)
            run("xcrun", "stapler", "validate", app)

        staging = work / "image"
        staging.mkdir()
        shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
        run("ditto", app, staging / "AgenticFan.app")
        (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
        staged_dmg = work / dmg.name
        run("hdiutil", "create", "-volname", "AgenticFan", "-srcfolder", staging,
            "-format", "UDZO", "-fs", "HFS+", staged_dmg)
        if args.identity:
            run("codesign", "--sign", args.identity, "--timestamp", staged_dmg)
        if args.notary_profile:
            notarize(staged_dmg, args.notary_profile)
            run("xcrun", "stapler", "staple", staged_dmg)
            run("xcrun", "stapler", "validate", staged_dmg)
        run("hdiutil", "verify", staged_dmg)
        mount = work / "mounted"
        mount.mkdir()
        attachment = run("hdiutil", "attach", staged_dmg, "-readonly", "-nobrowse",
                         "-mountpoint", mount, "-plist", capture_output=True)
        try:
            info = plistlib.loads(attachment.stdout)
            if not any(entity.get("mount-point") and Path(entity["mount-point"]).resolve() == mount
                       for entity in info["system-entities"]):
                raise RuntimeError("DMG did not mount at the expected path")
            if not (mount / "Applications").is_symlink() or os.readlink(mount / "Applications") != "/Applications":
                raise RuntimeError("DMG is missing the Applications installation link")
            for notice in (mount / "LICENSE", mount / "AgenticFan.app/Contents/Resources/LICENSE"):
                if notice.read_bytes() != (ROOT / "LICENSE").read_bytes():
                    raise RuntimeError("Packaged project license does not match source")
            run("codesign", "--verify", "--deep", "--strict", mount / "AgenticFan.app")
            smoke(mount / "AgenticFan.app/Contents/MacOS/agenticfan", work)
        finally:
            run("hdiutil", "detach", mount)
        shutil.copy2(staged_dmg, dmg)
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    dmg.with_suffix(".dmg.sha256").write_text(f"{digest}  {dmg.name}\n")
    metadata = {
        "artifact": dmg.name, "version": version, "architecture": "arm64", "minimum_macos": "15.0",
        "python": platform.python_version(), "pyinstaller": PyInstaller.__version__,
        "signing": "developer_id" if args.identity else "ad_hoc", "notarized": bool(args.notary_profile),
        "verified": ["project_license", "codesign", "hdiutil", "mounted_app", "bundled_cli", "hook_file_workflow", "demo_guardian", "demo_gui", "demo_temperature_quality", "demo_menu_bar_startup"],
        "hardware_verified": False, "sha256": digest,
    }
    dmg.with_suffix(".build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copy2(ROOT / "scripts/install-macos.sh", output / "install-macos.sh")
    shutil.copy2(ROOT / "INSTALL.md", output / "INSTALL.md")
    shutil.copy2(ROOT / "LICENSE", output / "LICENSE")
    print(dmg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
