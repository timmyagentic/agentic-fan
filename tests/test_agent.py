import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from macfan.agent import integrate, START
from macfan.doctor import diagnose
from macfan.errors import MacFanError


class AgentInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "user's path with spaces"
        self.root.mkdir()
        self.executable = Path(self.temp.name) / "app-binary"
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        self.executable.chmod(0o755)

    def test_install_both_agents_and_repeat_without_profile_duplication(self):
        profile = self.root / ".zprofile"
        profile.write_text("# user's original settings\n")
        result = integrate(self.executable, self.root, "both")
        self.assertEqual(len(result["skills"]), 2)
        self.assertEqual((self.root / ".local/bin/macfan").resolve(), self.executable)
        self.assertEqual((self.root / ".zprofile.before-macfan").read_text(), "# user's original settings\n")
        original = profile.read_text()
        self.assertEqual(original.count(START), 1)
        self.assertEqual(integrate(self.executable, self.root, "both")["profiles_updated"], [])
        self.assertEqual(profile.read_text(), original)
        self.assertFalse(result["hardware_settings_changed"])
        # A path containing spaces and quotes must remain one shell value.
        output = subprocess.check_output(["bash", "-c", '. "$1"; command -v macfan', "bash", str(profile)], text=True)
        self.assertEqual(output.strip(), str(self.root / ".local/bin/macfan"))

    def test_existing_unmanaged_skill_blocks_all_mutations(self):
        destination = self.root / ".agents/skills/macfan"
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("my own skill")
        with self.assertRaises(MacFanError) as raised:
            integrate(self.executable, self.root)
        self.assertEqual(raised.exception.code, "INSTALL_CONFLICT")
        self.assertFalse((self.root / ".local/bin/macfan").exists())
        self.assertFalse((self.root / ".zprofile").exists())
        self.assertEqual((destination / "SKILL.md").read_text(), "my own skill")

    def test_existing_cli_is_not_replaced(self):
        binary = self.root / ".local/bin/macfan"
        binary.parent.mkdir(parents=True)
        binary.write_text("user command")
        with self.assertRaises(MacFanError):
            integrate(self.executable, self.root)
        self.assertEqual(binary.read_text(), "user command")
        self.assertFalse((self.root / ".agents").exists())

    def test_modified_managed_skill_is_preserved(self):
        integrate(self.executable, self.root)
        installed = self.root / ".agents/skills/macfan/SKILL.md"
        installed.write_text(installed.read_text() + "\nUser customization\n")
        with self.assertRaises(MacFanError):
            integrate(self.executable, self.root)
        self.assertTrue(installed.read_text().endswith("User customization\n"))

    def test_symlink_profile_is_not_followed(self):
        original = Path(self.temp.name) / "real-profile"
        original.write_text("preserve")
        (self.root / ".zprofile").symlink_to(original)
        with self.assertRaises(MacFanError):
            integrate(self.executable, self.root)
        self.assertEqual(original.read_text(), "preserve")
        self.assertFalse((self.root / ".agents").exists())

    def test_malformed_receipt_does_not_replace_a_skill(self):
        integrate(self.executable, self.root)
        receipt = self.root / ".agents/skills/macfan/installation.json"
        receipt.write_text("[]")
        with self.assertRaises(MacFanError) as raised:
            integrate(self.executable, self.root)
        self.assertEqual(raised.exception.code, "INSTALL_CONFLICT")
        self.assertEqual(receipt.read_text(), "[]")

    def test_partial_write_failure_restores_original_profile_and_skills(self):
        from macfan.agent import atomic_write
        profile = self.root / ".zprofile"
        profile.write_text("original\n")

        def failing_write(path, content):
            if path.name == ".bash_profile":
                raise PermissionError("injected profile error")
            atomic_write(path, content)

        with patch("macfan.agent.atomic_write", side_effect=failing_write), self.assertRaises(PermissionError):
            integrate(self.executable, self.root)
        self.assertEqual(profile.read_text(), "original\n")
        self.assertFalse((self.root / ".agents/skills/macfan/SKILL.md").exists())
        self.assertFalse((self.root / ".local/bin/macfan").exists())


class DoctorTests(unittest.TestCase):
    def test_unavailable_hardware_still_has_an_actionable_diagnostic(self):
        error = MacFanError("UNAVAILABLE", "Native transport is absent", 3)
        with patch("macfan.hardware.native_hardware", side_effect=error):
            report = diagnose()
        self.assertEqual(report["status"], "unavailable")
        self.assertFalse(report["monitoring_available"])
        self.assertFalse(report["hardware_write_tested"])
        self.assertEqual(report["errors"][0]["code"], "UNAVAILABLE")

    def test_demo_diagnostics_are_explicit_and_do_not_change_fans(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"MACFAN_DEMO_DIR": directory}):
            report = subprocess.run([sys.executable, "-m", "macfan", "--demo", "doctor"],
                                    text=True, capture_output=True, check=True)
            message = json.loads(report.stdout)
            self.assertTrue(message["simulated"])
            self.assertTrue(message["data"]["monitoring_available"])
            self.assertFalse(message["data"]["hardware_write_tested"])
            state = subprocess.run([sys.executable, "-m", "macfan", "--demo", "fans"],
                                   text=True, capture_output=True, check=True)
            self.assertTrue(all(row["mode"] == "auto" for row in json.loads(state.stdout)["data"]["fans"]))


if __name__ == "__main__":
    unittest.main()
