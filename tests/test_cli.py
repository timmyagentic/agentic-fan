import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = dict(os.environ, MACFAN_DEMO_DIR=self.temp.name)
        self.base = [sys.executable, "-m", "macfan"]

    def run_cli(self, *args, demo=True):
        result = subprocess.run(self.base + (["--demo"] if demo else []) + list(args),
                                cwd=REPO, env=self.env, text=True, capture_output=True, timeout=10)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(result.stderr, "", result.stderr)
        return result.returncode, messages

    def start_control(self, duration="15"):
        process = subprocess.Popen(self.base + ["--demo", "fans", "set", "--fan", "0", "--rpm", "3000", "--duration", duration],
                                   cwd=REPO, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.cleanup_process, process)
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, "no control-started event")
        started = json.loads(process.stdout.readline())
        self.assertEqual(started.get("event"), "control_started", started)
        return process, started

    @staticmethod
    def cleanup_process(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)

    def test_schema_available_without_hardware(self):
        code, messages = self.run_cli("schema", demo=False)
        self.assertEqual(code, 0)
        manifest = messages[0]["data"]
        self.assertEqual(manifest["schema_version"], 1)
        self.assertIn("fans set", [x["command"] for x in manifest["commands"]])

    def test_snapshot_and_watch_are_explicit_simulations(self):
        code, messages = self.run_cli("snapshot")
        self.assertEqual(code, 0)
        self.assertTrue(messages[0]["simulated"])
        self.assertGreater(len(messages[0]["data"]["sensors"]), 0)
        code, messages = self.run_cli("watch", "--count", "2", "--interval", "0.2")
        self.assertEqual(code, 0)
        self.assertEqual(len(messages), 2)
        self.assertTrue(all(x["event"] == "sample" for x in messages))

    def test_invalid_arguments_are_json_and_nonzero(self):
        for args in (("watch", "--interval", "nan"), ("fans", "set", "--fan", "0", "--rpm", "9000", "--duration", "1"),
                     ("fans", "set", "--fan", "0", "--rpm", "3000", "--duration", "nan"),
                     ("fans", "auto", "--fan", "-1"), ("does-not-exist",)):
            code, messages = self.run_cli(*args)
            self.assertEqual(code, 2, messages)
            self.assertFalse(messages[-1]["ok"])
            self.assertEqual(messages[-1]["error"]["code"], "INVALID_ARGUMENT")

    @unittest.skipIf(sys.platform == "darwin", "Non-macOS rejection only")
    def test_native_does_not_silently_fall_back_to_demo(self):
        code, messages = self.run_cli("snapshot", demo=False)
        self.assertEqual(code, 3)
        self.assertFalse(messages[0]["simulated"])
        self.assertEqual(messages[0]["error"]["code"], "UNSUPPORTED_PLATFORM")

    def test_expiry_observes_rpm_and_restores(self):
        code, messages = self.run_cli("fans", "set", "--fan", "all", "--rpm", "3000", "--duration", "1")
        self.assertEqual(code, 0, messages)
        self.assertEqual([x["event"] for x in messages], ["control_started", "control_confirmed", "control_finished"])
        code, fans = self.run_cli("fans")
        self.assertTrue(all(x["mode"] == "auto" for x in fans[0]["data"]["fans"]))

    def test_auto_interrupts_an_active_session(self):
        process, _ = self.start_control()
        code, messages = self.run_cli("fans", "auto", "--fan", "all")
        self.assertEqual(code, 0, messages)
        output, error = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, error)
        events = [json.loads(x) for x in output.splitlines()]
        self.assertEqual(events[-1]["data"]["reason"], "external_auto")

    def test_parent_sigterm_returns_verified_restoration(self):
        process, _ = self.start_control()
        process.terminate()
        output, error = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130, error)
        events = [json.loads(x) for x in output.splitlines()]
        self.assertEqual(events[-1]["event"], "control_finished")
        self.assertTrue(events[-1]["data"]["automatic_mode_verified"])

    def test_parent_sigkill_still_restores(self):
        process, _ = self.start_control()
        process.kill()
        process.communicate(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            code, messages = self.run_cli("fans")
            if messages[0]["data"]["fans"][0]["mode"] == "auto":
                break
            time.sleep(0.05)
        else:
            self.fail("guardian did not restore after parent SIGKILL")

    def test_active_session_rejects_second_controller(self):
        process, _ = self.start_control()
        code, messages = self.run_cli("fans", "set", "--fan", "1", "--rpm", "3000", "--duration", "1")
        self.assertEqual(code, 6, messages)
        self.assertEqual(messages[-1]["error"]["code"], "CONTROL_BUSY")
        process.terminate()
        process.communicate(timeout=5)

    def test_killed_guardian_does_not_claim_restoration(self):
        process, started = self.start_control()
        os.kill(started["data"]["guardian_pid"], signal.SIGKILL)
        output, error = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 7, error)
        events = [json.loads(x) for x in output.splitlines()]
        self.assertEqual(events[-1]["error"]["code"], "RESTORE_UNCONFIRMED")
        code, messages = self.run_cli("fans", "auto", "--fan", "all")
        self.assertEqual(code, 0, messages)


if __name__ == "__main__":
    unittest.main()
