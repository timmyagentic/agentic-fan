import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from macfan import entry, gui, launch
from macfan.errors import MacFanError
from macfan.protocol import success


class DesktopTests(unittest.TestCase):
    def test_frozen_finder_launch_opens_gui_but_commands_stay_cli(self):
        with patch.object(sys, "frozen", True, create=True), patch("macfan.gui.main", return_value=0) as window:
            self.assertEqual(entry.main(["-psn_0_123"]), 0)
            window.assert_called_once_with(demo=False, smoke_test=False)
        with patch.object(sys, "frozen", True, create=True), patch("macfan.cli.main", return_value=0) as cli:
            self.assertEqual(entry.main(["--demo", "snapshot"]), 0)
            cli.assert_called_once_with(["--demo", "snapshot"])

    def test_frozen_guardian_has_its_own_entry_instead_of_reopening_gui(self):
        with patch.object(sys, "frozen", True, create=True), patch("macfan.guardian.main", return_value=0) as guardian:
            self.assertEqual(entry.main(launch.guardian_command()[1:]), 0)
            guardian.assert_called_once_with()

    def test_gui_does_not_accept_hidden_hardware_commands(self):
        with patch("macfan.gui.main") as window, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(entry.main(["--gui", "fans", "set"]), 2)
            window.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "INVALID_ARGUMENT")

    def test_gui_smoke_test_requires_simulated_hardware(self):
        with patch("macfan.gui.main") as window, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(["--gui", "--gui-smoke-test"]), 2)
            window.assert_not_called()

    def test_invalid_gui_control_input_never_reaches_a_subprocess(self):
        for rpm, duration in (("0", "1"), ("3000; touch bad", "1"), ("3000", "nan"), ("3000", "0"), ("3000", "3601")):
            with self.subTest(rpm=rpm, duration=duration), self.assertRaises(MacFanError):
                gui.control_arguments(0, rpm, duration)

    def test_gui_consumes_a_real_demo_session_and_verified_restore(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"MACFAN_DEMO_DIR": directory}):
            result = gui.run_control(gui.control_arguments(0, "3000", "1"), demo=True, timeout=10)
        self.assertTrue(result["simulated"])
        self.assertEqual(result["event"], "control_finished")
        self.assertTrue(result["data"]["automatic_mode_verified"])

    def test_gui_uses_normal_cli_after_helper_is_enabled(self):
        executable = "/Applications/A path with 'quotes'/MacFan.app/Contents/MacOS/macfan"
        reply = success({"automatic_mode_verified": True})
        completed = subprocess.CompletedProcess([], 0, json.dumps(reply) + "\r", "")
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "platform", "darwin"), \
                patch.object(sys, "executable", executable), patch("macfan.helper.status", return_value={"running": True, "compatible": True}), \
                patch("macfan.helper_install.manage") as manage, patch("macfan.gui.subprocess.run", return_value=completed) as run:
            gui.run_control(["fans", "auto", "--fan", "all"], demo=False, timeout=10)
        command = run.call_args.args[0]
        self.assertEqual(command, [executable, "fans", "auto", "--fan", "all"])
        manage.assert_not_called()
        self.assertNotIn("shell", run.call_args.kwargs)

    @unittest.skipUnless(sys.platform == "darwin", "Requires the macOS AppleScript compiler")
    def test_authorization_script_compiles_without_requesting_privileges(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["/usr/bin/osacompile", "-o", str(Path(directory) / "authorization.scpt"),
                            "-e", gui.AUTHORIZATION_SCRIPT], check=True, capture_output=True, text=True)

    def test_gui_does_not_claim_restore_if_child_only_started(self):
        started = success({"rpm": 3000}, simulated=True, event="control_started")
        completed = subprocess.CompletedProcess([], 0, json.dumps(started), "")
        with patch("macfan.gui.subprocess.run", return_value=completed), self.assertRaises(MacFanError) as raised:
            gui.run_control(["fans", "set"], demo=True, timeout=10)
        self.assertEqual(raised.exception.code, "RESTORE_UNCONFIRMED")

    @unittest.skipIf(sys.platform == "darwin", "Non-macOS build guard only")
    def test_dmg_builder_rejects_linux_without_creating_an_artifact(self):
        script = Path(__file__).resolve().parents[1] / "packaging/macos/build.py"
        spec = importlib.util.spec_from_file_location("macfan_dmg_build", script)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as output:
            destination = Path(directory) / "output"
            with self.assertRaises(SystemExit) as raised:
                builder.main(["--output", str(destination)])
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(destination.exists())
        self.assertIn("Apple Silicon Mac", output.getvalue())


if __name__ == "__main__":
    unittest.main()
