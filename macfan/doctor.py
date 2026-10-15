"""Read-only, structured installation and hardware diagnostics."""
from __future__ import annotations

import os
import platform
import sys

from . import __version__
from .errors import MacFanError


def diagnose(demo=False):
    report = {"version": __version__, "platform": sys.platform, "architecture": platform.machine(),
              "bundled": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
              "monitoring_available": False, "control_interface_available": False,
              "hardware_write_tested": False, "running_as_root": os.geteuid() == 0,
              "write_requires_root": not demo, "sensor_count": 0, "valid_sensor_count": 0,
              "fan_count": 0, "warnings": [], "errors": []}
    hw = None
    try:
        if demo:
            from .demo import demo_hardware
            hw = demo_hardware()
        else:
            from .hardware import native_hardware
            hw = native_hardware()
        sensors = hw.temperatures()
        fans = hw.fans()
        report.update(sensor_count=len(sensors), valid_sensor_count=sum(row["status"] == "ok" for row in sensors),
                      fan_count=len(fans), monitoring_available=any(row["status"] == "ok" for row in sensors),
                      control_interface_available=any(row["control_available"] for row in fans), warnings=hw.warnings)
        report["errors"].extend(error for row in fans for error in row["errors"])
        report["errors"].extend(row["error"] for row in sensors if "error" in row)
    except MacFanError as exc:
        report["errors"].append(exc.as_dict())
    except OSError as exc:
        report["errors"].append({"code": "IO_ERROR", "message": str(exc), "details": {}})
    finally:
        if hw:
            hw.close()
    report["status"] = "available" if report["monitoring_available"] and not report["errors"] else "limited" if report["monitoring_available"] else "unavailable"
    return report
