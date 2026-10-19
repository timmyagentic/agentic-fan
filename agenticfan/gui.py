"""Small desktop surface over the same monitoring and bounded-control core."""
from __future__ import annotations

import json
import subprocess
import sys

from .cli import snapshot
from .errors import AgenticFanError, invalid
from .launch import cli_command


from .helper_install import AUTHORIZATION_SCRIPT


def sensor_cells(row):
    """Keep rejected raw observations visible without presenting usable temperatures."""
    reasons = {"outside_plausible_range": "超出合理范围", "non_finite_reading": "非有限数值",
               "zero_reading": "零读数", "no_event": "无温度事件", "read_failed": "读取失败"}
    status = {"ok": "通过合理性检查", "invalid": "异常", "unverified": "待确认", "unavailable": "不可用"}.get(row["status"], "未知")
    if row.get("reason"):
        status += "：" + reasons.get(row["reason"], row["reason"])
    value = "—" if row["value"] is None else f"{row['value']:.1f}"
    raw = row.get("raw_value")
    raw_text = row.get("raw_non_finite", "—") if raw is None else f"{raw:.1f}"
    return row["name"], value, raw_text, row["source"], status


def control_arguments(fan_id: int, rpm: str, duration: str) -> list[str]:
    try:
        speed, seconds = int(rpm), int(duration)
    except ValueError:
        raise invalid("转速和持续时间必须是整数。") from None
    if speed <= 0 or not 1 <= seconds <= 3600:
        raise invalid("转速必须大于零，持续时间必须在 1～3600 秒之间。")
    return ["fans", "set", "--fan", str(fan_id), "--rpm", str(speed), "--duration", str(seconds)]


def run_control(arguments: list[str], demo: bool, timeout: float) -> dict:
    command = cli_command(*(["--demo"] if demo else []), *arguments)
    if not demo:
        if sys.platform != "darwin":
            raise AgenticFanError("UNSUPPORTED_PLATFORM", "真实硬件控制需要 macOS。", 3)
        from .helper import status
        from .helper_install import manage
        helper = status()
        if not helper.get("running") or not helper.get("compatible"):
            manage("install")
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    messages = [json.loads(line) for line in result.stdout.replace("\r\n", "\n").replace("\r", "\n").splitlines() if line.strip()]
    if result.returncode:
        if messages and not messages[-1].get("ok"):
            error = messages[-1]["error"]
            raise AgenticFanError(error["code"], error["message"], result.returncode, **error.get("details", {}))
        raise AgenticFanError("CONTROL_FAILED", result.stderr.strip() or "权限请求被取消，或控制命令失败。")
    if not messages or not messages[-1].get("ok") or not messages[-1].get("data", {}).get("automatic_mode_verified"):
        raise AgenticFanError("RESTORE_UNCONFIRMED", "没有收到自动模式恢复确认，请复查风扇状态。", 7)
    return messages[-1]


def main(demo=False, smoke_test=False):
    """Launch the bundled native surface; explicit commands remain JSON-first."""
    import os
    from pathlib import Path
    if sys.platform != "darwin":
        raise AgenticFanError("UNSUPPORTED_PLATFORM", "The native desktop UI requires macOS.", 3)
    frozen = getattr(sys, "frozen", False)
    native = (Path(sys.executable).parent / "AgenticFanUI" if frozen else
              Path(__file__).resolve().parents[1] / "build/native/AgenticFanUI")
    if not native.is_file():
        raise AgenticFanError("NATIVE_UI_MISSING", "Build the native UI with python3 packaging/macos/build_native.py, or reinstall AgenticFan.app.", 3)
    arguments = [str(native), "--backend", sys.executable]
    if not frozen:
        arguments.append("--module")
    if demo:
        arguments.append("--demo")
    if smoke_test:
        arguments.append("--smoke")
    os.execv(str(native), arguments)
    return 0
