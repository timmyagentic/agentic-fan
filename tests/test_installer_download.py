"""Exercise anonymous Release discovery without installing or touching hardware."""
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/timmyagentic/agentic-fan"


@unittest.skipUnless(sys.platform == "darwin" and platform.machine() == "arm64",
                     "The installer targets Apple Silicon macOS")
class InstallerDownloadTests(unittest.TestCase):
    def download(self, metadata, image=None):
        with tempfile.TemporaryDirectory(prefix="agenticfan-download-test-") as directory:
            work = Path(directory)
            tools = work / "bin"
            tools.mkdir()
            log = work / "requests.jsonl"
            metadata_file = work / "release.json"
            metadata_file.write_text(json.dumps(metadata))
            curl = tools / "curl"
            curl.write_text(f"#!{sys.executable}\n" + '''
import hashlib, json, os, pathlib, sys
args = sys.argv[1:]
url = args[-1]
with open(os.environ["DOWNLOAD_LOG"], "a") as log:
    log.write(json.dumps(url) + "\\n")
if url.endswith("/releases/latest"):
    content = pathlib.Path(os.environ["RELEASE_FIXTURE"]).read_text()
elif url.endswith(".dmg.sha256"):
    digest = hashlib.sha256(pathlib.Path(os.environ["DMG_FIXTURE"]).read_bytes()).hexdigest() if os.environ.get("DMG_FIXTURE") else "0" * 64
    content = digest + "  AgenticFan-0.4.2-arm64.dmg\\n"
elif url.endswith(".dmg"):
    if os.environ.get("DMG_FIXTURE"):
        pathlib.Path(args[args.index("--output") + 1]).write_bytes(pathlib.Path(os.environ["DMG_FIXTURE"]).read_bytes())
        sys.exit(0)
    content = "deliberately corrupt test image"
else:
    sys.exit(91)
if "--output" in args:
    pathlib.Path(args[args.index("--output") + 1]).write_text(content)
else:
    sys.stdout.write(content)
''')
            curl.chmod(0o755)
            gh = tools / "gh"
            gh.write_text('#!/bin/sh\nprintf "unexpected-gh\\n" >&2\nexit 99\n')
            gh.chmod(0o755)
            user = work / "untouched user"
            env = dict(os.environ, PATH=f"{tools}:/usr/bin:/bin:/usr/sbin:/sbin",
                       DOWNLOAD_LOG=str(log), RELEASE_FIXTURE=str(metadata_file),
                       DMG_FIXTURE=str(image) if image else "")
            result = subprocess.run(["/bin/bash", str(ROOT / "scripts/install-macos.sh"),
                                     "--user-root", str(user)], env=env, capture_output=True,
                                    text=True, timeout=30)
            requests = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            self.assertFalse(user.exists(), "A failed download must not create the installation target")
            self.assertNotIn("unexpected-gh", result.stderr, "Public installation must not invoke gh")
            return result, requests

    def test_default_download_pins_anonymous_stable_release_and_checks_hash(self):
        result, requests = self.download({"tag_name": "v0.4.2", "draft": False, "prerelease": False})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertEqual(requests, [
            "https://api.github.com/repos/timmyagentic/agentic-fan/releases/latest",
            f"{REPOSITORY}/releases/download/v0.4.2/AgenticFan-0.4.2-arm64.dmg",
            f"{REPOSITORY}/releases/download/v0.4.2/AgenticFan-0.4.2-arm64.dmg.sha256",
        ])

    def test_prerelease_metadata_is_rejected_before_downloading_assets(self):
        result, requests = self.download({"tag_name": "v0.4.2", "draft": False, "prerelease": True})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("published stable release", result.stderr)
        self.assertEqual(len(requests), 1)

    def test_malformed_tag_is_rejected_before_downloading_assets(self):
        result, requests = self.download({"tag_name": "../../other", "draft": False, "prerelease": False})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid stable release tag", result.stderr)
        self.assertEqual(len(requests), 1)

    def test_release_bundle_version_must_match_tag_before_installation(self):
        with tempfile.TemporaryDirectory(prefix="agenticfan-version-test-") as directory:
            root = Path(directory)
            contents = root / "payload" / "AgenticFan.app" / "Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": "io.github.timmyagentic.agenticfan",
                "CFBundleShortVersionString": "0.4.1",
            }))
            image = root / "mismatch.dmg"
            subprocess.run(["/usr/bin/hdiutil", "create", "-quiet", "-srcfolder",
                            str(root / "payload"), str(image)], check=True, timeout=30)
            result, requests = self.download(
                {"tag_name": "v0.4.2", "draft": False, "prerelease": False}, image)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Release app version does not match its tag", result.stderr)
            self.assertEqual(len(requests), 3)
