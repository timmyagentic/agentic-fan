"""Regression scenarios from installation feedback; no native hardware writes."""
import contextlib
import io
import json
import math
import struct
import unittest
from unittest.mock import Mock, patch

from macfan import cli
from macfan.codecs import KeyInfo
from macfan.demo import MemorySMC, fixture
from macfan.doctor import diagnose
from macfan.errors import MacFanError
from macfan.gui import sensor_cells
from macfan.hardware import Hardware
from macfan.hid import HIDTemperatures
from macfan.protocol import success


def fake_hid(observations):
    """Stub IOHID's event boundary, exercising the real read/classification path."""
    hid = HIDTemperatures.__new__(HIDTemperatures)
    hid.client = hid.services = None
    hid.entries = [(index, name, index + 1000) for index, (name, _) in enumerate(observations, 1)]
    values = {index: value for index, (_, value) in enumerate(observations, 1)}
    hid.event = Mock(side_effect=lambda service, *_: service if values[service] is not None else None)
    hid.event_value = Mock(side_effect=lambda event, _: values[event])
    hid.release = Mock()
    return hid


def hardware(observations=(), smc_temperatures=()):
    values = {key: value for key, value in fixture().items() if not key.startswith("T")}
    for key, value in smc_temperatures:
        values[key] = (KeyInfo("flt ", 4), struct.pack("<f", value))
    return Hardware(MemorySMC(values), hid=fake_hid(observations), simulated=True)


def reported_hardware():
    # 255 discovered, formerly 234 "valid": two of those were ~-9200°C.
    observations = [(f"Sensor {i}", 45.5) for i in range(230)]
    observations += [(f"Zero {i}", 0.0) for i in range(20)]
    observations += [("PMU tdev4", -9200.0), ("PMU tdev5", -9210.0), ("No event", None)]
    return hardware(observations, [("TCMz", 115.6), ("Tp0c", 115.6)])


class TemperatureTests(unittest.TestCase):
    def test_same_validation_at_both_source_boundaries(self):
        cases = [(-9200, "invalid"), (9200, "invalid"), (-40.25, "invalid"), (150.25, "invalid"),
                 (-40, "ok"), (150, "ok"), (-0.5, "ok"), (115.6, "ok"),
                 (0.0, "unverified"), (math.nan, "invalid"), (math.inf, "invalid"), (-math.inf, "invalid")]
        for value, status in cases:
            with self.subTest(value=value):
                hw = hardware([("PMU tdev4", value)], [("TCMz", value)])
                rows = hw.temperatures()
                self.assertEqual(len(rows), 2)
                for row in rows:
                    self.assertEqual(row["status"], status)
                    self.assertFalse(row["estimated"])
                    self.assertFalse(row["label_verified"])
                    if math.isfinite(value):
                        self.assertAlmostEqual(row["raw_value"], value, places=4)
                    else:
                        self.assertIsNone(row["raw_value"])
                        self.assertIn(row["raw_non_finite"], ("nan", "+infinity", "-infinity"))
                    if status == "ok":
                        self.assertAlmostEqual(row["value"], value, places=4)
                        self.assertIsNone(row["reason"])
                    else:
                        self.assertIsNone(row["value"])
                        self.assertIsNotNone(row["reason"])
                # Never leak NaN/Infinity into JSON, including the diagnostic evidence.
                json.dumps(success({"sensors": rows}), allow_nan=False)
                hw.hid.release.assert_called_once_with(1)

    def test_reported_255_sensor_snapshot_has_honest_counts_and_evidence(self):
        hw = reported_hardware()
        data = cli.snapshot(hw)
        with patch("macfan.hardware.native_hardware", return_value=hw):
            report = diagnose()
        summary = data["sensor_summary"]
        self.assertEqual(summary, report["sensor_summary"])
        for name, count in {"total": 255, "readable": 254, "ok": 232, "invalid": 2, "unverified": 20, "unavailable": 1}.items():
            self.assertEqual(summary[name], count, name)
        self.assertEqual(summary["by_source"]["smc"]["ok"], 2)
        self.assertEqual(summary["by_source"]["iohid"]["ok"], 230)
        self.assertEqual(report["valid_sensor_count"], 232)
        self.assertEqual(report["status"], "limited")
        self.assertEqual(report["data_quality"], "partial")
        self.assertTrue(report["temperature_interface_available"])
        self.assertTrue(report["monitoring_available"])
        self.assertTrue(report["control_interface_available"])
        self.assertFalse(report["hardware_write_tested"])
        self.assertTrue(data["partial"])
        self.assertEqual({warning["code"] for warning in data["warnings"]},
                         {"TEMPERATURE_INVALID", "TEMPERATURE_UNVERIFIED", "TEMPERATURE_UNAVAILABLE"})
        self.assertEqual(len(report["sensor_issues"]), 23)
        bad = {row["name"]: row for row in report["sensor_issues"] if row["status"] == "invalid"}
        self.assertEqual(bad["PMU tdev4"]["raw_value"], -9200)
        self.assertEqual(bad["PMU tdev5"]["reason"], "outside_plausible_range")
        self.assertIsNone(bad["PMU tdev4"]["value"])
        self.assertEqual(hw.smc.writes, [])
        for row in data["sensors"][:2]:
            self.assertIn(row["name"], ("TCMz", "Tp0c"))
            self.assertAlmostEqual(row["value"], 115.6, places=4)
            self.assertFalse(row["label_verified"])

    def test_successful_interface_with_no_usable_temperatures_is_not_available(self):
        hw = hardware([("bad", -9200), ("zero", 0)])
        with patch("macfan.hardware.native_hardware", return_value=hw):
            report = diagnose()
        self.assertTrue(report["temperature_interface_available"])
        self.assertFalse(report["monitoring_available"])
        self.assertEqual(report["data_quality"], "unusable")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["valid_sensor_count"], 0)
        self.assertEqual(len(report["warnings"]), 2)

    def test_missing_event_is_distinct_from_zero_and_invalid(self):
        hw = hardware([("missing", None)])
        with patch("macfan.hardware.native_hardware", return_value=hw):
            report = diagnose()
        self.assertFalse(report["temperature_interface_available"])
        self.assertEqual(report["data_quality"], "unavailable")
        row = report["sensor_issues"][0]
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(row["reason"], "no_event")
        self.assertIsNone(row["raw_value"])
        self.assertEqual(report["errors"][0]["code"], "TEMPERATURE_EVENT_UNAVAILABLE")
        hw.hid.release.assert_not_called()

    def test_repeated_sampling_recovers_without_stale_values_or_accumulated_warnings(self):
        hw = hardware(smc_temperatures=[("TCMz", 45)])
        states = [(45, "ok"), (-9200, "invalid"), (0, "unverified"), (48, "ok")]
        for value, status in states:
            hw.smc.values["TCMz"] = (KeyInfo("flt ", 4), struct.pack("<f", value))
            data = cli.snapshot(hw)
            self.assertEqual(data["sensors"][0]["status"], status)
            self.assertEqual(data["sensors"][0]["value"], value if status == "ok" else None)
            self.assertEqual(len(data["warnings"]), int(status != "ok"))
        del hw.smc.values["TCMz"]
        missing = cli.snapshot(hw)["sensors"][0]
        self.assertEqual(missing["status"], "unavailable")
        self.assertIsNone(missing["value"])
        self.assertIsNone(missing["raw_value"])
        self.assertEqual(hw.warnings, [])

    def test_malformed_smc_payload_is_read_failure_not_a_plausibility_failure(self):
        hw = hardware(smc_temperatures=[("TCMz", 45)])
        hw.smc.values["TCMz"] = (KeyInfo("flt ", 4), b"\x00")
        row = hw.temperatures()[0]
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(row["reason"], "read_failed")
        self.assertEqual(row["error"]["code"], "UNSUPPORTED_ENCODING")
        self.assertIsNone(row["raw_value"])

    def test_doctor_preserves_temperatures_when_fan_discovery_fails(self):
        hw = hardware(smc_temperatures=[("TCMz", 45)])
        with patch("macfan.hardware.native_hardware", return_value=hw), \
                patch.object(hw, "fans", side_effect=MacFanError("SMC_IO_ERROR", "fan read failed")):
            report = diagnose()
        self.assertEqual(report["valid_sensor_count"], 1)
        self.assertTrue(report["monitoring_available"])
        self.assertEqual(report["data_quality"], "complete")
        self.assertEqual(report["status"], "limited")
        self.assertEqual(report["errors"][0]["code"], "SMC_IO_ERROR")

    def test_doctor_still_discovers_fans_when_temperature_discovery_fails(self):
        hw = hardware()
        with patch("macfan.hardware.native_hardware", return_value=hw), \
                patch.object(hw, "temperatures", side_effect=MacFanError("SMC_IO_ERROR", "sensor enumeration failed")):
            report = diagnose()
        self.assertTrue(report["control_interface_available"])
        self.assertEqual(report["fan_count"], 2)
        self.assertFalse(report["monitoring_available"])
        self.assertEqual(report["errors"][0]["code"], "SMC_IO_ERROR")
        self.assertEqual(hw.smc.writes, [])

    def test_fanless_mac_and_all_plausible_readings_can_be_available(self):
        hw = hardware(smc_temperatures=[("TCMz", 45)])
        hw.smc.values["FNum"] = (KeyInfo("ui8 ", 1), b"\0")
        with patch("macfan.hardware.native_hardware", return_value=hw):
            report = diagnose()
        self.assertEqual(report["status"], "available")
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["errors"], [])
        self.assertFalse(report["control_interface_available"])

    def test_source_warning_prevents_an_all_clear_diagnosis(self):
        hw = hardware(smc_temperatures=[("TCMz", 45)])
        hw.warnings = [{"code": "HID_UNAVAILABLE", "message": "IOHID cannot be opened"}]
        with patch("macfan.hardware.native_hardware", return_value=hw):
            report = diagnose()
        self.assertEqual(report["status"], "limited")
        self.assertEqual(report["data_quality"], "complete")
        self.assertEqual(report["warnings"][0]["code"], "HID_UNAVAILABLE")

    def test_cli_commands_share_quality_semantics_and_source_filtering(self):
        for arguments in (["sensors"], ["snapshot"], ["watch", "--count", "2", "--interval", "0.2"],
                          ["capabilities"], ["doctor"], ["sensors", "--source", "smc"]):
            with self.subTest(arguments=arguments), \
                    patch("macfan.cli.native_hardware", return_value=reported_hardware()), \
                    patch("macfan.hardware.native_hardware", return_value=reported_hardware()), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(arguments), 0)
                messages = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(len(messages), 2 if arguments[0] == "watch" else 1)
                for message in messages:
                    self.assertEqual(message["schema_version"], 2)
                    data = message["data"]
                    filtered = arguments[-1] == "smc"
                    self.assertEqual(data["sensor_summary"]["invalid"], 0 if filtered else 2)
                    self.assertEqual(data["partial"], not filtered)
                    self.assertEqual(len(data["warnings"]), 0 if filtered else 3)

    def test_gui_keeps_rejected_evidence_out_of_usable_temperature_column(self):
        rows = hardware([("PMU tdev4", -9200), ("zero", 0), ("missing", None), ("nonfinite", math.inf)],
                        [("TCMz", 115.6)]).temperatures()
        cells = {row["name"]: sensor_cells(row) for row in rows}
        self.assertEqual(cells["TCMz"][1:3], ("115.6", "115.6"))
        self.assertEqual(cells["PMU tdev4"][1:3], ("—", "-9200.0"))
        self.assertIn("异常", cells["PMU tdev4"][-1])
        self.assertEqual(cells["zero"][1:3], ("—", "0.0"))
        self.assertIn("待确认", cells["zero"][-1])
        self.assertEqual(cells["missing"][1:3], ("—", "—"))
        self.assertEqual(cells["nonfinite"][1:3], ("—", "+infinity"))


if __name__ == "__main__":
    unittest.main()
