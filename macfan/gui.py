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
            raise MacFanError("UNSUPPORTED_PLATFORM", "真实硬件控制需要 macOS。", 3)
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
    root.geometry("980x760")
    root.minsize(800, 680)
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

    overview_frame = ttk.Frame(frame)
    overview_frame.pack(fill="x", pady=(0, 10))
    overview_values = {}
    for column, (group, title) in enumerate((("cpu", "CPU 处理器"), ("gpu", "GPU 图形处理器"), ("storage", "SSD / 闪存"))):
        box = ttk.LabelFrame(overview_frame, text=title, padding=(14, 8))
        box.grid(row=0, column=column, sticky="nsew", padx=(0, 8))
        overview_frame.columnconfigure(column, weight=1)
        value = tk.StringVar(value="—")
        caption = tk.StringVar(value="尚无已识别测点")
        ttk.Label(box, textvariable=value, font=("Helvetica", 24, "bold")).pack(anchor="w")
        ttk.Label(box, textvariable=caption).pack(anchor="w")
        overview_values[group] = (value, caption)

    toolbar = ttk.Frame(frame)
    toolbar.pack(fill="x", pady=(0, 6))
    ttk.Label(toolbar, text="按部件查看", font=("Helvetica", 13, "bold")).pack(side="left")
    advanced = tk.BooleanVar(value=False)
    latest_sensors = []
    detail = tk.StringVar(value="点击部件左侧箭头展开测点；选择测点可查看原始名称和命名依据。")
    ttk.Checkbutton(toolbar, text="高级：显示原始编号与未识别测点", variable=advanced,
                    command=lambda: render_sensors()).pack(side="right")
    holder = ttk.Frame(frame)
    holder.pack(fill="both", expand=True, pady=(0, 6))
    sensors = ttk.Treeview(holder, columns=("value", "count", "raw", "source"),
                          show="tree headings", height=8, selectmode="browse")
    sensors.heading("#0", text="部件 / 温度测点")
    sensors.column("#0", width=290, minwidth=240)
    for column, title, width in (("value", "温度 °C", 90), ("count", "读数状态", 150),
                                 ("raw", "原始标识", 170), ("source", "命名依据", 130)):
        sensors.heading(column, text=title)
        sensors.column(column, width=width, minwidth=70)
    scroll = ttk.Scrollbar(holder, orient="vertical", command=sensors.yview)
    sensors.configure(yscrollcommand=scroll.set)
    sensors.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    ttk.Label(frame, textvariable=detail, wraplength=920).pack(anchor="w", pady=(0, 8))

    def render_sensors():
        from .sensor_labels import GROUPS, overview
        summaries = {group['group']: group for group in overview(latest_sensors)}
        sensors.configure(displaycolumns=("value", "count", "raw", "source") if advanced.get() else ("value", "count"))
        available = max(sensors.winfo_width() - 4, 700)
        widths = (("#0", .36), ("value", .12), ("count", .18), ("raw", .21), ("source", .13)) if advanced.get() else (("#0", .50), ("value", .18), ("count", .32))
        for column, proportion in widths:
            sensors.column(column, width=int(available * proportion))
        wanted = set()
        basis = {"system_name": "系统部件名称", "community_mapping": "公开机型映射", "simulated": "演示数据", "unknown": "未识别"}
        for group, title in GROUPS.items():
            members = [row for row in latest_sensors if row.get('group', 'unknown') == group]
            if not members or (group == 'unknown' and not advanced.get()):
                continue
            parent = 'group:' + group
            wanted.add(parent)
            summary = summaries.get(group)
            value = summary['value'] if summary else None
            cells = ("—" if value is None else f"{value:.1f}", f"{len(members)} 个测点 · 最高值" if summary else f"{len(members)} 个未识别测点", "", "")
            if sensors.exists(parent):
                sensors.item(parent, text=title, values=cells)
            else:
                sensors.insert('', 'end', iid=parent, text=title, values=cells, open=False)
            import re
            def order(row):
                return [(1, int(part)) if part.isdigit() else (0, part) for part in re.split(r'(\d+)', row.get('display_name', row['name']))]
            for row in sorted(members, key=order):
                identity = row['id']; wanted.add(identity)
                cells = sensor_cells(row)
                values = (cells[1], cells[4], row['name'], basis.get(row.get('label_status'), '未识别'))
                if sensors.exists(identity):
                    sensors.item(identity, text=row.get('display_name', row['name']), values=values)
                else:
                    sensors.insert(parent, 'end', iid=identity, text=row.get('display_name', row['name']), values=values)
        for parent in sensors.get_children():
            if parent not in wanted:
                sensors.delete(parent)
            else:
                for child in sensors.get_children(parent):
                    if child not in wanted:
                        sensors.delete(child)
        for group, (value_text, caption) in overview_values.items():
            summary = summaries.get(group)
            value = summary['value'] if summary else None
            value_text.set('—' if value is None else f'{value:.1f} °C')
            caption.set(f"已识别测点最高 · {summary['valid_count']}/{summary['sensor_count']} 个可用" if summary else "尚无已识别测点")

    def sensor_selected(_=None):
        selected = sensors.selection()
        row = next((item for item in latest_sensors if selected and item['id'] == selected[0]), None)
        if row:
            raw_value = row.get('raw_value')
            raw_text = row.get('raw_non_finite', '—') if raw_value is None else f'{raw_value:.2f} °C'
            detail.set(f"{row.get('display_name', row['name'])} · 原始名称 {row['name']} · {row['source'].upper()} · 原始读数 {raw_text}\n" + row.get('label_note', '名称待确认。'))
        else:
            detail.set("分组温度为当前已识别、可用测点的最高值；展开后可查看各测点。")
    sensors.bind('<<TreeviewSelect>>', sensor_selected)

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
    operation_status = tk.StringVar(value="首次控扇需授权安装 Helper，之后无需重复授权。到期恢复系统自动。")
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
        operation_status.set("正在执行；若尚未启用 Helper，系统会请求一次安装授权…" if not demo else "正在运行模拟控扇会话…")

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
                    latest_sensors[:] = data["sensors"]
                    render_sensors()
                    fan_rows.clear()
                    fan_rows.update({row["id"]: row for row in data["fans"]})
                    update_rows(fans, [(row["id"], (row["name"], "—" if row["actual_rpm"] is None else f"{row['actual_rpm']:.0f}",
                        "—" if row["min_rpm"] is None or row["max_rpm"] is None else f"{row['min_rpm']:g}–{row['max_rpm']:g}",
                        {"auto": "系统自动", "manual": "手动", "unknown": "未知"}[row["mode"]])) for row in data["fans"]])
                    if not fans.selection() and fans.get_children():
                        fans.selection_set(fans.get_children()[0])
                    prefix = "演示数据 · " if demo else ""
                    counts = data["sensor_summary"]
                    warning = " · 诊断有提示" if data["warnings"] or any(row["errors"] for row in data["fans"]) else ""
                    identified = data.get("identified_sensor_count", 0)
                    status.set(f"{prefix}已识别 {identified} 个测点 · 其余 {counts['total'] - identified} 个可在高级视图查看 · "
                               f"{counts['invalid']} 异常 / {counts['unverified']} 待确认 / {counts['unavailable']} 不可用")
                elif kind == "read_error":
                    read_failed = True
                    latest_sensors.clear()
                    render_sensors()
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
        if smoke_result == 0:
            parent = sensors.get_children()[0]
            if not sensors.get_children(parent):
                smoke_result = 1
            sensors.item(parent, open=True)
            render_sensors()
            if not sensors.item(parent, 'open'):
                smoke_result = 1
            advanced.set(True)
            render_sensors()
            if 'raw' not in sensors.cget('displaycolumns'):
                smoke_result = 1
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
