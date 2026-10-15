"""Shared plausibility checks, not hardware limits or calibration guarantees."""
from __future__ import annotations

import math


POLICY = {
    "id": "mac-temperature-plausibility-v1",
    "minimum_celsius": -40.0,
    "maximum_celsius": 150.0,
    "bounds_inclusive": True,
    "zero_status": "unverified",
    "description": "Broad software plausibility bounds, not Apple specifications or safe operating limits. "
                   "Passing does not verify calibration, physical location, or thermal safety.",
}
STATUSES = ("ok", "invalid", "unverified", "unavailable")


def reading(value=None, *, unavailable_reason="read_failed") -> dict:
    """Only usable observations enter value; preserve rejected evidence separately."""
    result = {"value": None, "raw_value": None, "status": "unavailable", "reason": unavailable_reason}
    if value is None:
        return result
    if not math.isfinite(value):
        result.update(status="invalid", reason="non_finite_reading",
                      raw_non_finite="nan" if math.isnan(value) else "+infinity" if value > 0 else "-infinity")
        return result
    result["raw_value"] = value
    if not POLICY["minimum_celsius"] <= value <= POLICY["maximum_celsius"]:
        result.update(status="invalid", reason="outside_plausible_range")
    elif value == 0:
        result.update(status="unverified", reason="zero_reading")
    else:
        result.update(value=value, status="ok", reason=None)
    return result


def _counts(rows) -> dict:
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] += 1
    counts.update(total=len(rows), readable=len(rows) - counts["unavailable"])
    counts["data_quality"] = ("complete" if counts["ok"] and counts["ok"] == counts["total"] else
                              "partial" if counts["ok"] else
                              "unusable" if counts["readable"] else "unavailable")
    return counts


def sensor_report(rows, warnings=()) -> dict:
    """Build current-sample statistics and warnings without mutating hardware state."""
    summary = _counts(rows)
    summary["by_source"] = {source: _counts([row for row in rows if row["source"] == source])
                            for source in sorted({row["source"] for row in rows})}
    summary["validation_policy"] = dict(POLICY)
    notices = list(warnings)
    for status, code, message in (
        ("invalid", "TEMPERATURE_INVALID", "Temperature readings failed plausibility checks; inspect raw values and reasons."),
        ("unverified", "TEMPERATURE_UNVERIFIED", "Zero readings are unverified and excluded from usable temperatures."),
        ("unavailable", "TEMPERATURE_UNAVAILABLE", "Some temperature interfaces did not return a reading."),
    ):
        ids = [row["id"] for row in rows if row["status"] == status]
        if ids:
            notices.append({"code": code, "message": message, "details": {"count": len(ids), "sensor_ids": ids}})
    if not rows:
        notices.append({"code": "NO_TEMPERATURE_SENSORS", "message": "No temperature sensors were discovered.", "details": {}})
    return {"sensors": rows, "sensor_summary": summary, "warnings": notices,
            "temperature_interface_available": summary["readable"] > 0,
            "monitoring_available": summary["ok"] > 0,
            "partial": summary["data_quality"] != "complete" or bool(warnings)}
