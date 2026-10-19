import ctypes
import math
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agenticfan.codecs import KeyInfo, decode, encode_rpm, key_number
from agenticfan.demo import MemorySMC, fixture
from agenticfan.errors import AgenticFanError, MissingKey
from agenticfan.guardian import run_session, validate_duration
from agenticfan.hardware import Hardware
from agenticfan.runtime import lock, runtime_directory
from agenticfan.smc import SMCData


class CodecTests(unittest.TestCase):
    def test_signed_temperature(self):
        self.assertEqual(decode(KeyInfo("sp78", 2), b"\xff\x80"), -0.5)
        self.assertEqual(decode(KeyInfo("sp78", 2), b"\x32\x80"), 50.5)

    def test_rpm_round_trip_and_byte_order(self):
        self.assertEqual(encode_rpm(KeyInfo("fpe2", 2), 3000), bytes.fromhex("2ee0"))
        for info in (KeyInfo("fpe2", 2), KeyInfo("flt ", 4)):
            for rpm in (0, 1200, 6200, 16383):
                self.assertEqual(decode(info, encode_rpm(info, rpm)), rpm)

    def test_nonfinite_and_invalid_payloads(self):
        for value in (math.inf, -math.inf, math.nan):
            with self.assertRaises(AgenticFanError):
                decode(KeyInfo("flt ", 4), struct.pack("<f", value))
        for info, raw in ((KeyInfo("sp78", 4), b"\0" * 4), (KeyInfo("flt ", 4), b"\0"), (KeyInfo("junk", 2), b"\0\0")):
            with self.assertRaises(AgenticFanError):
                decode(info, raw)
        with self.assertRaises(AgenticFanError):
            encode_rpm(KeyInfo("fpe2", 2), 16384)

    def test_key_and_abi(self):
        self.assertEqual(key_number("F0Ac"), 0x46304163)
        for bad in ("F00Ac", "F", "温度测试"):
            with self.assertRaises(AgenticFanError):
                key_number(bad)
        self.assertEqual(ctypes.sizeof(SMCData), 80)
        self.assertEqual(SMCData.payload.offset, 48)
        self.assertEqual(SMCData.index.offset, 44)


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.smc = MemorySMC()
        self.hw = Hardware(self.smc, simulated=True)

    def test_actual_mixed_temperature_types_and_failed_read(self):
        self.smc.values["TN01"] = (KeyInfo("sp78", 2), b"\xff\x80")
        self.smc.values["Tbad"] = (KeyInfo("flt ", 4), struct.pack("<f", math.nan))
        rows = {row["id"]: row for row in self.hw.temperatures()}
        self.assertEqual(rows["smc:TN01"]["value"], -0.5)
        self.assertEqual(rows["smc:TCDX"]["value"], 47.5)
        self.assertEqual(rows["smc:Tbad"]["status"], "invalid")
        self.assertIsNone(rows["smc:Tbad"]["value"])
        self.assertEqual(rows["smc:Tp00"]["status"], "unverified")
        self.assertFalse(any(row["estimated"] for row in rows.values()))

    def test_read_failure_does_not_reuse_stale_temperature(self):
        self.hw.temperatures()
        del self.smc.values["TCDX"]
        rows = {row["id"]: row for row in self.hw.temperatures()}
        self.assertEqual(rows["smc:TCDX"]["status"], "unavailable")
        self.assertIsNone(rows["smc:TCDX"]["value"])

    def test_upper_and_lowercase_modes(self):
        self.smc.values["F0md"] = self.smc.values.pop("F0Md")
        self.hw.validate_control([0, 1], 3200)
        self.hw.apply([0, 1], 3200)
        self.assertEqual([self.hw.mode(i) for i in (0, 1)], ["manual", "manual"])
        self.hw.restore([0, 1])
        self.assertEqual([self.hw.mode(i) for i in (0, 1)], ["auto", "auto"])
        self.assertNotIn("Ftst", [key for key, _ in self.smc.writes])

    def test_legacy_bitmap_preserves_other_bits_and_high_byte(self):
        del self.smc.values["F0Md"]
        self.smc.values["FS! "] = (KeyInfo("ui16", 2), (1 << 9).to_bytes(2, "big"))
        self.hw._set_mode(0, True)
        self.assertEqual(int.from_bytes(self.smc.read("FS! "), "big"), (1 << 9) | 1)
        self.hw._set_mode(8, True)
        self.assertEqual(self.hw.mode(8), "manual")
        self.hw._set_mode(0, False)
        self.assertEqual(int.from_bytes(self.smc.read("FS! "), "big"), (1 << 9) | (1 << 8))

    def test_all_targets_validated_before_writes(self):
        self.smc.values["F1Mx"] = (KeyInfo("flt ", 4), struct.pack("<f", 2500))
        with self.assertRaises(AgenticFanError) as caught:
            self.hw.validate_control([0, 1], 3000)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self.smc.writes, [])

    def test_already_manual_and_unknown_fans_rejected(self):
        self.smc.write("F0Md", b"\1")
        with self.assertRaises(AgenticFanError) as caught:
            self.hw.validate_control([0], 3000)
        self.assertEqual(caught.exception.code, "CONTROL_CONFLICT")
        with self.assertRaises(AgenticFanError):
            self.hw.validate_control([12], 3000)

    def test_failed_probe_is_not_absence(self):
        original = self.smc.info

        def probe(key):
            if key == "F0md":
                raise AgenticFanError("SMC_IO_ERROR", "transport down")
            return original(key)

        self.smc.info = probe
        with self.assertRaises(AgenticFanError) as caught:
            self.hw.validate_control([0], 3000)
        self.assertEqual(caught.exception.code, "SMC_IO_ERROR")
        self.assertEqual(self.smc.writes, [])

    def test_experimental_ftst_is_explicit_and_restored(self):
        self.hw.validate_control([0], 3000, True)
        self.hw.apply([0], 3000, True)
        self.assertEqual(self.smc.read("Ftst"), b"\1")
        self.hw.restore([0], True)
        self.assertEqual(self.smc.read("Ftst"), b"\0")

    def test_ftst_does_not_clear_under_another_manual_fan(self):
        self.smc.write("F1Md", b"\1")
        self.smc.write("Ftst", b"\1")
        with self.assertRaises(AgenticFanError) as caught:
            self.hw.restore([0], True)
        self.assertEqual(caught.exception.code, "RESTORE_FAILED")
        self.assertEqual(self.smc.read("Ftst"), b"\1")

    def test_normal_restore_reports_active_ftst_instead_of_claiming_success(self):
        self.smc.write("Ftst", b"\1")
        with self.assertRaises(AgenticFanError) as caught:
            self.hw.restore([0, 1])
        self.assertEqual(caught.exception.code, "RESTORE_FAILED")
        self.assertEqual(caught.exception.details["failures"][0]["code"], "FTST_STILL_ACTIVE")
        self.assertEqual(self.smc.read("Ftst"), b"\1")

    def test_does_not_start_when_global_ftst_is_already_active(self):
        self.smc.write("Ftst", b"\1")
        self.smc.writes.clear()
        with self.assertRaises(AgenticFanError) as caught:
            self.hw.validate_control([0], 3000)
        self.assertEqual(caught.exception.code, "CONTROL_CONFLICT")
        self.assertEqual(self.smc.writes, [])

    def test_restore_attempts_other_fans_after_failure(self):
        original = self.smc.write
        self.smc.write("F0Md", b"\1")
        self.smc.write("F1Md", b"\1")

        def write(key, raw):
            if key == "F0Md":
                raise AgenticFanError("IO_FAIL", "failed")
            original(key, raw)

        self.smc.write = write
        with self.assertRaises(AgenticFanError) as caught:
            self.hw.restore([0, 1])
        self.assertEqual(caught.exception.exit_code, 7)
        self.assertEqual(self.hw.mode(1), "auto")

    def test_native_writes_need_privilege(self):
        self.hw.simulated = False
        with patch("agenticfan.hardware.os.geteuid", return_value=501):
            with self.assertRaises(AgenticFanError) as caught:
                self.hw.validate_control([0], 3000)
        self.assertEqual(caught.exception.code, "PERMISSION_DENIED")


class FakeClock:
    def __init__(self):
        self.time = 0.0
        self.cancel = False

    def now(self):
        return self.time

    def wait(self, delay):
        self.time += delay
        return self.cancel


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.smc = MemorySMC()
        self.hw = Hardware(self.smc, simulated=True)
        self.clock = FakeClock()
        self.events = []

    def run_control(self, fans=None, ftst=False, wait=None):
        run_session(self.hw, Path(self.temp.name), [0] if fans is None else fans,
                    3000, 2, ftst, self.events.append, wait or self.clock.wait,
                    self.clock.now, self.clock.now)

    def test_expiry_restores_all_modes(self):
        self.run_control([0, 1], ftst=True)
        self.assertEqual([e["event"] for e in self.events], ["control_started", "control_confirmed", "control_finished"])
        self.assertEqual(self.events[-1]["data"]["reason"], "expired")
        self.assertEqual(self.hw.mode(0), "auto")
        self.assertEqual(self.hw.mode(1), "auto")
        self.assertEqual(self.smc.read("Ftst"), b"\0")

    def test_partial_apply_failure_rolls_back(self):
        original = self.smc.write

        def write(key, raw):
            if key == "F1Tg":
                raise AgenticFanError("IO_FAIL", "injected partial write failure")
            original(key, raw)

        self.smc.write = write
        with self.assertRaises(AgenticFanError) as caught:
            self.run_control([0, 1], ftst=True)
        self.assertEqual(caught.exception.code, "IO_FAIL")
        self.assertEqual([self.hw.mode(i) for i in (0, 1)], ["auto", "auto"])
        self.assertEqual(self.smc.read("Ftst"), b"\0")

    def test_rpm_not_reaching_target_is_a_failure_and_restores(self):
        original = self.smc.write

        def write(key, raw):
            old = self.smc.read("F0Ac")
            original(key, raw)
            self.smc.values["F0Ac"] = (KeyInfo("flt ", 4), old)

        self.smc.write = write
        with self.assertRaises(AgenticFanError) as caught:
            self.run_control()
        self.assertEqual(caught.exception.code, "CONTROL_NOT_CONFIRMED")
        self.assertEqual(self.hw.mode(0), "auto")

    def test_cancellation(self):
        def wait(delay):
            self.clock.time += delay
            return delay > 0

        self.run_control(wait=wait)
        self.assertEqual(self.events[-1]["data"]["reason"], "cancelled")
        self.assertEqual(self.hw.mode(0), "auto")

    def test_observation_gap_restores(self):
        def wait(delay):
            if delay:
                self.clock.time += 10
            return False

        self.run_control(wait=wait)
        self.assertEqual(self.events[-1]["data"]["reason"], "observation_gap")
        self.assertEqual(self.hw.mode(0), "auto")

    def test_preexisting_manual_not_reset_after_validation_failure(self):
        self.smc.write("F0Md", b"\1")
        with self.assertRaises(AgenticFanError):
            self.run_control()
        self.assertEqual(self.hw.mode(0), "manual")

    def test_restore_failure_takes_precedence(self):
        original = self.smc.write

        def write(key, raw):
            if key == "F0Md" and raw == b"\0":
                raise AgenticFanError("IO_FAIL", "cannot restore")
            original(key, raw)

        self.smc.write = write
        with self.assertRaises(AgenticFanError) as caught:
            self.run_control()
        self.assertEqual(caught.exception.code, "RESTORE_FAILED")
        self.assertFalse(any(e["event"] == "control_finished" for e in self.events))

    def test_second_session_cannot_start(self):
        with lock(Path(self.temp.name), "session.lock"):
            with self.assertRaises(AgenticFanError) as caught:
                self.run_control()
        self.assertEqual(caught.exception.code, "CONTROL_BUSY")
        self.assertEqual(self.smc.writes, [])

    def test_target_changed_by_another_writer_ends_control(self):
        def wait(delay):
            self.clock.time += delay
            if self.clock.time >= 1:
                self.smc.write("F0Tg", struct.pack("<f", 4000))
            return False

        with self.assertRaises(AgenticFanError) as caught:
            self.run_control(wait=wait)
        self.assertEqual(caught.exception.code, "CONTROL_LOST")
        self.assertEqual(self.hw.mode(0), "auto")

    def test_duration_cannot_disable_expiry(self):
        for value in (0, -1, 3601, math.inf, math.nan, True):
            with self.assertRaises(AgenticFanError):
                validate_duration(value)


if __name__ == "__main__":
    unittest.main()
