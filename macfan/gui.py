"""Small desktop surface over the same monitoring and bounded-control core."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading

from .cli import snapshot
from .errors import MacFanError, invalid
from .launch import cli_command


AUTHORIZATION_SCRIPT = '''on run argv
    set commandText to ""
    repeat with argument in argv
        set commandText to commandText & quoted form of (argument as text) & " "
    end repeat
    return do shell script commandText with administrator privileges
end run'''


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
            raise MacFanError("UNSUPPORTED_PLATFORM", "真实硬件控制需要 macOS。", 3)
        # osascript receives an argv array; AppleScript quotes each argument.
        # No password is read, stored, or handled by the Python UI.
        command = ["/usr/bin/osascript", "-e", AUTHORIZATION_SCRIPT, *command]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    messages = [json.loads(line) for line in result.stdout.replace("\r\n", "\n").replace("\r", "\n").splitlines() if line.strip()]
    if result.returncode:
        if messages and not messages[-1].get("ok"):
            error = messages[-1]["error"]
            raise MacFanError(error["code"], error["message"], result.returncode, **error.get("details", {}))
        raise MacFanError("CONTROL_FAILED", result.stderr.strip() or "权限请求被取消，或控制命令失败。")
    if not messages or not messages[-1].get("ok") or not messages[-1].get("data", {}).get("automatic_mode_verified"):
        raise MacFanError("RESTORE_UNCONFIRMED", "没有收到自动模式恢复确认，请复查风扇状态。", 7)
    return messages[-1]


def main(demo=False, smoke_test=False):
    # CLI commands do not import or initialize Tk.
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("MacFan · 温度与风扇" + (" · 演示" if demo else ""))
    root.geometry("900x650")
    root.minsize(700, 520)
    events = queue.Queue()
    stop = threading.Event()
    pending = set()
    fan_rows = {}
    loaded = False
    read_failed = False
    smoke_result = 1
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="MacFan", font=("Helvetica", 24, "bold")).pack(anchor="w")
    ttk.Label(frame, text="查看温度，为风扇设置一段时间的固定转速。").pack(anchor="w", pady=(4, 12))
    status = tk.StringVar(value="正在连接传感器…" if not demo else "演示模式：以下均为模拟数据。")
    ttk.Label(frame, textvariable=status, wraplength=830).pack(anchor="w", pady=(0, 10))

    def table(parent, columns, headings, height):
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True, pady=(0, 12))
        tree = ttk.Treeview(holder, columns=columns, show="headings", height=height, selectmode="browse")
        for column, heading in zip(columns, headings):
            tree.heading(column, text=heading)
            tree.column(column, width=130, stretch=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    ttk.Label(frame, text="温度传感器", font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))
    sensors = table(frame, ("name", "value", "source", "status"), ("传感器", "温度 °C", "数据源", "状态"), 10)
    ttk.Label(frame, text="风扇", font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))
    fans = table(frame, ("name", "rpm", "range", "mode"), ("风扇", "当前 RPM", "允许范围 RPM", "模式"), 3)
    controls = ttk.Frame(frame)
    controls.pack(fill="x", pady=(0, 8))
    rpm = tk.StringVar(value="3000")
    duration = tk.StringVar(value="30")
    ttk.Label(controls, text="目标 RPM").pack(side="left")
    ttk.Entry(controls, textvariable=rpm, width=8).pack(side="left", padx=(6, 15))
    ttk.Label(controls, text="持续秒数").pack(side="left")
    ttk.Entry(controls, textvariable=duration, width=6).pack(side="left", padx=(6, 15))
    operation_status = tk.StringVar(value="选择一把风扇后设置。控制到期后恢复系统自动模式。")
    ttk.Label(frame, textvariable=operation_status, wraplength=830).pack(anchor="w")

    def read_worker():
        hw = None
        try:
            if demo:
                from .demo import demo_hardware
                hw = demo_hardware()
            else:
                from .hardware import native_hardware
                hw = native_hardware()
            while not stop.is_set():
                events.put(("snapshot", snapshot(hw)))
                stop.wait(1)
        except Exception as exc:
            events.put(("read_error", str(exc)))
        finally:
            if hw:
                hw.close()

    def submit(action, arguments, timeout):
        pending.add(action)
        apply_button.configure(state="disabled")
        if action == "auto":
            auto_button.configure(state="disabled")
        operation_status.set("等待系统授权或控制完成…" if not demo else "正在运行模拟控扇会话…")

        def work():
            try:
                run_control(arguments, demo, timeout)
                events.put(("operation", (action, None)))
            except Exception as exc:
                events.put(("operation", (action, str(exc))))
        threading.Thread(target=work, daemon=True).start()

    def apply():
        selection = fans.selection()
        if not selection or pending or read_failed:
            return
        fan = fan_rows[int(selection[0])]
        try:
            arguments = control_arguments(fan["id"], rpm.get(), duration.get())
            if not fan["control_available"]:
                raise invalid("这把风扇当前没有可用的控制接口。")
            if not fan["min_rpm"] <= int(rpm.get()) <= fan["max_rpm"]:
                raise invalid(f"请选择 {fan['min_rpm']:g}～{fan['max_rpm']:g} RPM。")
        except MacFanError as exc:
            messagebox.showerror("无法设置", exc.message, parent=root)
            return
        submit("set", arguments, int(duration.get()) + 180)

    def automatic():
        if "auto" not in pending and fan_rows:
            submit("auto", ["fans", "auto", "--fan", "all"], 180)

    apply_button = ttk.Button(controls, text="设置选中风扇", command=apply, state="disabled")
    apply_button.pack(side="left", padx=(0, 8))
    auto_button = ttk.Button(controls, text="全部恢复自动", command=automatic, state="disabled")
    auto_button.pack(side="left")

    def update_rows(tree, rows):
        existing = set(tree.get_children())
        for identity, values in rows:
            identity = str(identity)
            if identity in existing:
                tree.item(identity, values=values)
            else:
                tree.insert("", "end", iid=identity, values=values)
            existing.discard(identity)
        for identity in existing:
            tree.delete(identity)

    def drain():
        nonlocal loaded, read_failed
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == "snapshot":
                    loaded = True
                    update_rows(sensors, [(row["id"], (row["name"], "—" if row["value"] is None else f"{row['value']:.1f}",
                        row["source"], {"ok": "已读取", "unverified": "待确认", "unavailable": "不可用"}[row["status"]])) for row in data["sensors"]])
                    fan_rows.clear()
                    fan_rows.update({row["id"]: row for row in data["fans"]})
                    update_rows(fans, [(row["id"], (row["name"], "—" if row["actual_rpm"] is None else f"{row['actual_rpm']:.0f}",
                        "—" if row["min_rpm"] is None or row["max_rpm"] is None else f"{row['min_rpm']:g}–{row['max_rpm']:g}",
                        {"auto": "系统自动", "manual": "手动", "unknown": "未知"}[row["mode"]])) for row in data["fans"]])
                    if not fans.selection() and fans.get_children():
                        fans.selection_set(fans.get_children()[0])
                    prefix = "演示数据 · " if demo else ""
                    warning = " · 部分读数待确认" if data["partial"] else ""
                    status.set(f"{prefix}{len(data['sensors'])} 个温度测点 · {len(data['fans'])} 把风扇{warning}")
                elif kind == "read_error":
                    read_failed = True
                    update_rows(sensors, [])
                    update_rows(fans, [])
                    status.set("读取失败：" + data)
                elif kind == "operation":
                    action, error = data
                    pending.discard(action)
                    operation_status.set("操作失败：" + error if error else "已确认恢复系统自动模式。")
                    if error:
                        messagebox.showerror("风扇控制", error, parent=root)
                apply_button.configure(state="normal" if fan_rows and not pending and not read_failed else "disabled")
                auto_button.configure(state="normal" if fan_rows and "auto" not in pending else "disabled")
        except queue.Empty:
            pass
        if not stop.is_set():
            root.after(100, drain)

    def close():
        if pending:
            messagebox.showinfo("控扇操作尚未结束", "请先恢复自动模式，或等待本次操作完成后再退出。系统授权对话框中的请求也可以取消。", parent=root)
            return
        stop.set()
        root.destroy()

    def smoke_check():
        nonlocal smoke_result
        smoke_result = 0 if loaded and sensors.get_children() and fans.get_children() else 1
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    if sys.platform == "darwin":
        root.createcommand("tk::mac::Quit", close)
    threading.Thread(target=read_worker, daemon=True).start()
    root.after(100, drain)
    if smoke_test:
        root.after(3000, smoke_check)
    root.mainloop()
    return smoke_result if smoke_test else 0
