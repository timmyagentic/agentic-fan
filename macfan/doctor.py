"""Read-only, structured installation and hardware diagnostics."""
from __future__ import annotations

import os
import platform
import sys

from . import __version__
from .errors import MacFanError
from .temperatures import sensor_report


def diagnose(demo=False):
    report = {"version": __version__, "platform": sys.platform, "architecture": platform.machine(),
              "bundled": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
              "monitoring_available": False, "control_interface_available": False,
              "hardware_write_tested": False, "running_as_root": os.geteuid() == 0,
              "write_requires_root": not demo,
              "fan_count": 0, "warnings": [], "errors": []}
    hw = None
    sensors = []
    try:
        if demo:
            from .demo import demo_hardware
            hw = demo_hardware()
        else:
            from .hardware import native_hardware
            hw = native_hardware()
        # Preserve successful observations even if the other subsystem fails.
        try:
            sensors = hw.temperatures()
            report["errors"].extend(row["error"] for row in sensors if "error" in row)
        except (MacFanError, OSError) as exc:
            report["errors"].append(_error(exc))
        try:
            fans = hw.fans()
            report.update(fan_count=len(fans), control_interface_available=any(row["control_available"] for row in fans))
            report["errors"].extend(error for row in fans for error in row["errors"])
        except (MacFanError, OSError) as exc:
            report["errors"].append(_error(exc))
        report["warnings"] = list(hw.warnings)
    except MacFanError as exc:
        report["errors"].append(exc.as_dict())
    except OSError as exc:
        report["errors"].append({"code": "IO_ERROR", "message": str(exc), "details": {}})
    finally:
        if hw:
            hw.close()
    temperatures = sensor_report(sensors, report["warnings"])
    temperatures.pop("sensors")
    report.update(temperatures)
    summary = report["sensor_summary"]
    report.update(sensor_count=summary["total"], valid_sensor_count=summary["ok"],
                  data_quality=summary["data_quality"],
                  sensor_issues=[row for row in sensors if row["status"] != "ok"])
    report["partial"] = report["partial"] or bool(report["errors"])
    report["status"] = ("unavailable" if not report["monitoring_available"] else
                        "limited" if report["partial"] else "available")
    return report


def _error(exc):
    return exc.as_dict() if isinstance(exc, MacFanError) else {"code": "IO_ERROR", "message": str(exc), "details": {}}
