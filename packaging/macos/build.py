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
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def smoke(executable: Path, directory: Path, gui=True):
    """Exercise the bundled runtime outside the checkout, without system Python."""
    env = dict(os.environ, MACFAN_DEMO_DIR=str(directory / "demo"), PATH="/usr/bin:/bin:/usr/sbin:/sbin")
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
    if gui:
        run(executable, "--gui", "--demo", "--gui-smoke-test", cwd=directory, env=env, timeout=30)


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
    # Fail before building if this Python installation lacks the UI runtime.
    import tkinter  # noqa: F401
    import PyInstaller

    version = runpy.run_path(str(ROOT / "macfan" / "__init__.py"))["__version__"]
    flavor = "" if args.notary_profile else "-signed-unnotarized" if args.identity else "-dev"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    dmg = output / f"MacFan-{version}-arm64{flavor}.dmg"
    if dmg.exists():
        parser.error(f"Output already exists; choose a clean --output directory: {dmg}")
    with tempfile.TemporaryDirectory(prefix="macfan-build-") as temporary:
        work = Path(temporary).resolve()
        products = work / "products"
        env = dict(os.environ)
        env["PYINSTALLER_CONFIG_DIR"] = str(work / "pyinstaller-cache")
        env.pop("MACFAN_CODESIGN_IDENTITY", None)
        if args.identity:
            env["MACFAN_CODESIGN_IDENTITY"] = args.identity
        run(sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
            "--distpath", products, "--workpath", work / "build", ROOT / "packaging/macos/macfan.spec",
            cwd=ROOT, env=env)
        app = products / "MacFan.app"
        executable = app / "Contents/MacOS/macfan"
        if not executable.is_file():
            raise RuntimeError("PyInstaller did not produce the expected application bundle")
        signing = ["--options", "runtime", "--timestamp"] if args.identity else []
        run("codesign", "--force", "--sign", args.identity or "-", *signing, app)
        run("codesign", "--verify", "--deep", "--strict", app)
        smoke(executable, work)
        if args.notary_profile:
            archive = work / "MacFan.zip"
            run("ditto", "-c", "-k", "--keepParent", app, archive)
            notarize(archive, args.notary_profile)
            run("xcrun", "stapler", "staple", app)
            run("xcrun", "stapler", "validate", app)

        staging = work / "image"
        staging.mkdir()
        run("ditto", app, staging / "MacFan.app")
        (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
        staged_dmg = work / dmg.name
        run("hdiutil", "create", "-volname", "MacFan", "-srcfolder", staging,
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
            run("codesign", "--verify", "--deep", "--strict", mount / "MacFan.app")
            smoke(mount / "MacFan.app/Contents/MacOS/macfan", work)
        finally:
            run("hdiutil", "detach", mount)
        shutil.copy2(staged_dmg, dmg)
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    dmg.with_suffix(".dmg.sha256").write_text(f"{digest}  {dmg.name}\n")
    metadata = {
        "artifact": dmg.name, "version": version, "architecture": "arm64", "minimum_macos": "15.0",
        "python": platform.python_version(), "pyinstaller": PyInstaller.__version__,
        "signing": "developer_id" if args.identity else "ad_hoc", "notarized": bool(args.notary_profile),
        "verified": ["codesign", "hdiutil", "mounted_app", "bundled_cli", "demo_guardian", "demo_gui"],
        "hardware_verified": False, "sha256": digest,
    }
    dmg.with_suffix(".build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(dmg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
