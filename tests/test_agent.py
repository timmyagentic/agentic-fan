import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agenticfan.agent import integrate, START
from agenticfan import __version__
from agenticfan.doctor import diagnose
from agenticfan.errors import AgenticFanError


class AgentInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS exposes /var as a symlink to /private/var; compare canonical
        # identities, as the installer does for executable and home paths.
        temporary = Path(self.temp.name).resolve()
        self.root = temporary / "user's path with spaces"
        self.root.mkdir()
        self.executable = temporary / "app-binary"
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        self.executable.chmod(0o755)

    def test_install_both_agents_and_repeat_without_profile_duplication(self):
        profile = self.root / ".zprofile"
        profile.write_text("# user's original settings\n")
        result = integrate(self.executable, self.root, "both")
        self.assertEqual(len(result["skills"]), 2)
        self.assertEqual((self.root / ".local/bin/agenticfan").resolve(), self.executable)
        self.assertEqual((self.root / ".zprofile.before-agenticfan").read_text(), "# user's original settings\n")
        original = profile.read_text()
        self.assertEqual(original.count(START), 1)
        self.assertEqual(integrate(self.executable, self.root, "both")["profiles_updated"], [])
        self.assertEqual(profile.read_text(), original)
        self.assertFalse(result["hardware_settings_changed"])
        # A path containing spaces and quotes must remain one shell value.
        output = subprocess.check_output(["bash", "-c", '. "$1"; command -v agenticfan', "bash", str(profile)], text=True)
        self.assertEqual(output.strip(), str(self.root / ".local/bin/agenticfan"))

    def test_existing_unmanaged_skill_blocks_all_mutations(self):
        destination = self.root / ".agents/skills/agenticfan"
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("my own skill")
        with self.assertRaises(AgenticFanError) as raised:
            integrate(self.executable, self.root)
        self.assertEqual(raised.exception.code, "INSTALL_CONFLICT")
        self.assertFalse((self.root / ".local/bin/agenticfan").exists())
        self.assertFalse((self.root / ".zprofile").exists())
        self.assertEqual((destination / "SKILL.md").read_text(), "my own skill")

    def test_upgrade_refreshes_unmodified_managed_skill_and_receipt(self):
        old_skill = Path(self.temp.name) / "old-skill.md"
        old_skill.write_text("Old AgenticFan sensor contract\n")
        with patch("agenticfan.agent.SKILL", old_skill), patch("agenticfan.agent.__version__", "0.1.0"):
            integrate(self.executable, self.root, "both")
        profile = (self.root / ".zprofile").read_bytes()
        result = integrate(self.executable, self.root, "both")
        self.assertEqual(result["profiles_updated"], [])
        self.assertEqual((self.root / ".zprofile").read_bytes(), profile)
        for folder in (".agents", ".claude"):
            directory = self.root / folder / "skills/agenticfan"
            self.assertIn("schema_version: 2", (directory / "SKILL.md").read_text())
            self.assertEqual(json.loads((directory / "installation.json").read_text())["version"], __version__)

    def test_existing_cli_is_not_replaced(self):
        binary = self.root / ".local/bin/agenticfan"
        binary.parent.mkdir(parents=True)
        binary.write_text("user command")
        with self.assertRaises(AgenticFanError):
            integrate(self.executable, self.root)
        self.assertEqual(binary.read_text(), "user command")
        self.assertFalse((self.root / ".agents").exists())

    def test_modified_managed_skill_is_preserved(self):
        integrate(self.executable, self.root)
        installed = self.root / ".agents/skills/agenticfan/SKILL.md"
        installed.write_text(installed.read_text() + "\nUser customization\n")
        with self.assertRaises(AgenticFanError):
            integrate(self.executable, self.root)
        self.assertTrue(installed.read_text().endswith("User customization\n"))

    def test_symlink_profile_is_not_followed(self):
        original = Path(self.temp.name) / "real-profile"
        original.write_text("preserve")
        (self.root / ".zprofile").symlink_to(original)
        with self.assertRaises(AgenticFanError):
            integrate(self.executable, self.root)
        self.assertEqual(original.read_text(), "preserve")
        self.assertFalse((self.root / ".agents").exists())

    def test_malformed_receipt_does_not_replace_a_skill(self):
        integrate(self.executable, self.root)
        receipt = self.root / ".agents/skills/agenticfan/installation.json"
        receipt.write_text("[]")
        with self.assertRaises(AgenticFanError) as raised:
            integrate(self.executable, self.root)
        self.assertEqual(raised.exception.code, "INSTALL_CONFLICT")
        self.assertEqual(receipt.read_text(), "[]")

    def test_partial_write_failure_restores_original_profile_and_skills(self):
        from agenticfan.agent import atomic_write
        profile = self.root / ".zprofile"
        profile.write_text("original\n")

        def failing_write(path, content):
            if path.name == ".bash_profile":
                raise PermissionError("injected profile error")
            atomic_write(path, content)

        with patch("agenticfan.agent.atomic_write", side_effect=failing_write), self.assertRaises(PermissionError):
            integrate(self.executable, self.root)
        self.assertEqual(profile.read_text(), "original\n")
        self.assertFalse((self.root / ".agents/skills/agenticfan/SKILL.md").exists())
        self.assertFalse((self.root / ".local/bin/agenticfan").exists())


class DoctorTests(unittest.TestCase):
    def test_unavailable_hardware_still_has_an_actionable_diagnostic(self):
        error = AgenticFanError("UNAVAILABLE", "Native transport is absent", 3)
        with patch("agenticfan.hardware.native_hardware", side_effect=error):
            report = diagnose()
        self.assertEqual(report["status"], "unavailable")
        self.assertFalse(report["monitoring_available"])
        self.assertFalse(report["hardware_write_tested"])
        self.assertEqual(report["errors"][0]["code"], "UNAVAILABLE")

    def test_demo_diagnostics_are_explicit_and_do_not_change_fans(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AGENTICFAN_DEMO_DIR": directory}):
            report = subprocess.run([sys.executable, "-m", "agenticfan", "--demo", "doctor"],
                                    text=True, capture_output=True, check=True)
            message = json.loads(report.stdout)
            self.assertTrue(message["simulated"])
            self.assertTrue(message["data"]["monitoring_available"])
            self.assertFalse(message["data"]["hardware_write_tested"])
            state = subprocess.run([sys.executable, "-m", "agenticfan", "--demo", "fans"],
                                   text=True, capture_output=True, check=True)
            self.assertTrue(all(row["mode"] == "auto" for row in json.loads(state.stdout)["data"]["fans"]))


if __name__ == "__main__":
    unittest.main()
