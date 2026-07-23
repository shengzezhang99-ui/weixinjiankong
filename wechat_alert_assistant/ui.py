from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .alarm import AlarmPlayer
from .config import Config, load_config, save_config
from .logging_setup import setup_logging
from .models import Alert, Message
from .monitor import WindowMonitor, diagnose_ocr_imports
from .notifier import send_notification
from .rules import match_rules, parse_message


def show_topmost_message(parent: tk.Misc, level: str, title: str, message: str) -> None:
    previous_topmost = False
    try:
        previous_topmost = bool(parent.attributes("-topmost"))  # type: ignore[attr-defined]
        parent.lift()
        parent.attributes("-topmost", True)  # type: ignore[attr-defined]
        parent.update_idletasks()
    except Exception:
        pass

    try:
        if level == "warning":
            messagebox.showwarning(title, message, parent=parent)
        elif level == "error":
            messagebox.showerror(title, message, parent=parent)
        else:
            messagebox.showinfo(title, message, parent=parent)
    finally:
        try:
            parent.attributes("-topmost", previous_topmost)  # type: ignore[attr-defined]
        except Exception:
            pass


class AlertWindow(tk.Toplevel):
    def __init__(
        self,
        master: "MainWindow",
        alert: Alert,
        on_resolve,
        on_delay,
        on_notify,
    ):
        super().__init__(master)
        self.alert = alert
        self.on_resolve = on_resolve
        self.on_delay = on_delay
        self.on_notify = on_notify
        self.title("微信强提醒")
        self.geometry("540x380")
        self.minsize(500, 340)
        self.resizable(True, True)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.header = ttk.Label(self, text="微信强提醒", font=("Microsoft YaHei", 18, "bold"))
        self.header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))

        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=18)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        ttk.Label(body, text="群：").grid(row=0, column=0, sticky="nw", pady=4)
        ttk.Label(body, text=alert.message.chat_name or "未识别", wraplength=420).grid(
            row=0, column=1, sticky="ew", pady=4
        )
        ttk.Label(body, text="内容：").grid(row=1, column=0, sticky="nw", pady=4)
        content_frame = ttk.Frame(body)
        content_frame.grid(row=1, column=1, sticky="nsew", pady=4)
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(0, weight=1)
        self.content_text = tk.Text(content_frame, height=6, wrap="word", relief="solid", borderwidth=1)
        self.content_text.grid(row=0, column=0, sticky="nsew")
        content_scrollbar = ttk.Scrollbar(content_frame, orient="vertical", command=self.content_text.yview)
        content_scrollbar.grid(row=0, column=1, sticky="ns")
        self.content_text.configure(yscrollcommand=content_scrollbar.set)
        self.content_text.insert("1.0", alert.message.content or alert.message.raw_text)
        self.content_text.configure(state="disabled")

        self.elapsed_var = tk.StringVar()
        self.deadline_var = tk.StringVar()
        ttk.Label(body, textvariable=self.elapsed_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 2))
        ttk.Label(body, textvariable=self.deadline_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=2)

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 16))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        buttons.columnconfigure(2, weight=1)
        ttk.Button(buttons, text="我已处理", command=self._resolve).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(buttons, text="延迟5分钟", command=self._delay).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(buttons, text="立即通知", command=self._notify).grid(row=0, column=2, sticky="ew", padx=(6, 0))

        self._tick()
        self.lift()
        self.focus_force()

    def _tick(self) -> None:
        now = datetime.now()
        elapsed = max(0, int((now - self.alert.created_at).total_seconds()))
        remaining = max(0, int((self.alert.deadline_at - now).total_seconds()))
        self.elapsed_var.set(f"已提醒：{elapsed // 60:02d}:{elapsed % 60:02d}")
        self.deadline_var.set(f"将在：{remaining // 60:02d}:{remaining % 60:02d} 后通知联系人")
        if self.alert.status in ("pending", "delayed"):
            self.after(1000, self._tick)

    def _resolve(self) -> None:
        self.on_resolve(self.alert)
        self.destroy()

    def _delay(self) -> None:
        self.on_delay(self.alert)

    def _notify(self) -> None:
        self.on_notify(self.alert)


class RegionSelector(tk.Toplevel):
    def __init__(self, master: "MainWindow", on_selected):
        super().__init__(master)
        self.on_selected = on_selected
        self.start_x = 0
        self.start_y = 0
        self.rect_id = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.28)
        self.configure(bg="black")
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.cursor = "crosshair"

        self.canvas = tk.Canvas(self, cursor="crosshair", bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            24,
            24,
            anchor="nw",
            fill="white",
            font=("Microsoft YaHei", 16, "bold"),
            text="按住鼠标左键拖拽框选监控区域，Esc 取消",
        )
        self.canvas.bind("<ButtonPress-1>", self._start)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._finish)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.focus_force()

    def _start(self, event) -> None:
        self.start_x = event.x_root
        self.start_y = event.y_root
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00ff88", width=3)

    def _drag(self, event) -> None:
        if not self.rect_id:
            return
        self.canvas.coords(
            self.rect_id,
            self.start_x,
            self.start_y,
            event.x_root,
            event.y_root,
        )

    def _finish(self, event) -> None:
        left = min(self.start_x, event.x_root)
        top = min(self.start_y, event.y_root)
        right = max(self.start_x, event.x_root)
        bottom = max(self.start_y, event.y_root)
        if right - left < 20 or bottom - top < 20:
            show_topmost_message(self, "warning", "框选区域", "选择区域太小，请重新框选")
            return
        self.on_selected([int(left), int(top), int(right), int(bottom)])
        self.destroy()


class MainWindow(ctk.CTk):
    def __init__(self, config: Config, logger: logging.Logger):
        super().__init__()
        self.config = config
        self.logger = logger
        self.alarm = AlarmPlayer(logger)
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.monitor: WindowMonitor | None = None
        self.current_alerts: dict[str, Alert] = {}
        self.pages: dict[str, ttk.Frame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.current_page = ""

        self.title("微信强提醒助手")
        self.geometry("980x720")
        self.minsize(900, 650)
        self.protocol("WM_DELETE_WINDOW", self._hide_window)

        self._build_vars()
        self._configure_style()
        self._build_ui()
        self._refresh_dependency_status()
        self.after(200, self._process_events)
        self.logger.info("软件启动")

    def _build_vars(self) -> None:
        self.status_var = tk.StringVar(value="未启动")
        self.dep_status_var = tk.StringVar()
        self.notify_status_var = tk.StringVar(value="通知状态：未执行")
        self.monitored_chats_text = None
        self.my_names_text = None
        self.keywords_text = None
        self.escalation_var = tk.IntVar(value=self.config.app.escalation_minutes)
        self.poll_interval_var = tk.IntVar(value=self.config.app.poll_interval_ms)
        self.alarm_sound_var = tk.StringVar(value=self.config.app.alarm_sound)
        self.contact_name_var = tk.StringVar(value=self.config.notification.contact_name)
        self.automation_pos_var = tk.StringVar()
        self.automation_press_enter_var = tk.BooleanVar(value=self.config.notification.automation_press_enter)
        self.allow_keyword_without_chat_var = tk.BooleanVar(value=self.config.rules.allow_keyword_without_chat)
        self.debug_screenshots_var = tk.BooleanVar(value=self.config.app.debug_save_screenshots)
        self.popup_ocr_enabled_var = tk.BooleanVar(value=self.config.wechat.enable_popup_ocr)
        self.region_enabled_var = tk.BooleanVar(value=self.config.region.enabled)
        self.region_require_chat_var = tk.BooleanVar(value=self.config.region.require_chat_match)
        self.region_interval_var = tk.IntVar(value=self.config.region.poll_interval_seconds)
        self.region_bbox_var = tk.StringVar()
        self.automation_template_text = None
        self.test_text = None

    def _build_ui(self) -> None:
        root = ctk.CTkFrame(self, fg_color="#f3f4f6", corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(root, width=214, fg_color="#0f172a", corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(20, 18))
        ctk.CTkLabel(
            brand,
            text="微信强提醒",
            text_color="#ffffff",
            font=("Microsoft YaHei", 16, "bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="OCR 监控助手",
            text_color="#94a3b8",
            font=("Microsoft YaHei", 9),
        ).pack(anchor="w", pady=(4, 0))

        nav = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav.pack(fill="x", padx=10)
        for key, label in [
            ("rules", "规则配置"),
            ("region", "区域监控"),
            ("notify", "通知与报警"),
            ("test", "测试工具"),
            ("logs", "运行日志"),
        ]:
            self.nav_buttons[key] = self._nav_button(nav, key, label)
            self.nav_buttons[key].pack(fill="x", pady=3)

        sidebar_footer = ctk.CTkFrame(sidebar, fg_color="#111827", corner_radius=12)
        sidebar_footer.pack(side="bottom", fill="x", padx=14, pady=16)
        ctk.CTkLabel(
            sidebar_footer,
            textvariable=self.status_var,
            text_color="#d1d5db",
            font=("Microsoft YaHei", 9),
            anchor="w",
        ).pack(fill="x", padx=12, pady=10)

        main = ctk.CTkFrame(root, fg_color="#f3f4f6", corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(main, fg_color="#f3f4f6", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="微信强提醒助手",
            text_color="#111827",
            font=("Microsoft YaHei", 20, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            textvariable=self.dep_status_var,
            text_color="#92400e",
            font=("Microsoft YaHei", 9),
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ctk.CTkLabel(
            header,
            textvariable=self.notify_status_var,
            text_color="#4b5563",
            font=("Microsoft YaHei", 9),
        ).grid(row=2, column=0, sticky="w", pady=(3, 0))

        action_bar = ctk.CTkFrame(header, fg_color="transparent")
        action_bar.grid(row=0, column=1, rowspan=3, sticky="e")
        ctk.CTkButton(action_bar, text="保存配置", width=96, height=34, command=self.save_from_ui).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            action_bar,
            text="启动监控",
            width=96,
            height=34,
            fg_color="#059669",
            hover_color="#047857",
            command=self.start_monitoring,
        ).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            action_bar,
            text="停止监控",
            width=96,
            height=34,
            fg_color="#e5e7eb",
            hover_color="#d1d5db",
            text_color="#111827",
            command=self.stop_monitoring,
        ).pack(side="left", padx=(8, 0))

        content_shell = ctk.CTkFrame(main, fg_color="#ffffff", corner_radius=16)
        content_shell.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0, 18))
        content_shell.grid_columnconfigure(0, weight=1)
        content_shell.grid_rowconfigure(0, weight=1)

        content = ttk.Frame(content_shell, style="Surface.TFrame", padding=18)
        content.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        self.pages = {
            "rules": self._rules_tab(content),
            "region": self._region_tab(content),
            "notify": self._notify_tab(content),
            "test": self._test_tab(content),
            "logs": self._log_tab(content),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")
        self._update_region_label()
        self._update_automation_pos_label()
        self._show_page("rules")

    def _configure_style(self) -> None:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#f3f4f6")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Microsoft YaHei", 10))
        style.configure("Surface.TFrame", background="#ffffff")
        style.configure("TFrame", background="#ffffff")
        style.configure("TLabel", background="#ffffff", foreground="#1f2937")
        style.configure("TCheckbutton", background="#ffffff", foreground="#1f2937")
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor="#d1d5db", lightcolor="#d1d5db")
        style.configure("TButton", padding=(14, 7), background="#eef2f7", foreground="#111827", borderwidth=0)
        style.map("TButton", background=[("active", "#e5e7eb")])
        style.configure("Primary.TButton", background="#2563eb", foreground="#ffffff")
        style.map("Primary.TButton", background=[("active", "#1d4ed8")], foreground=[("active", "#ffffff")])
        style.configure("Success.TButton", background="#059669", foreground="#ffffff")
        style.map("Success.TButton", background=[("active", "#047857")], foreground=[("active", "#ffffff")])
        style.configure("TCombobox", padding=(8, 5))
        style.configure("TSpinbox", padding=(8, 5))

    def _nav_button(self, parent: tk.Misc, key: str, label: str) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=label,
            command=lambda: self._show_page(key),
            anchor="w",
            height=40,
            corner_radius=10,
            fg_color="transparent",
            hover_color="#1e293b",
            text_color="#cbd5e1",
            font=("Microsoft YaHei", 10),
        )

    def _show_page(self, key: str) -> None:
        self.current_page = key
        self.pages[key].tkraise()
        for nav_key, button in self.nav_buttons.items():
            if nav_key == key:
                button.configure(fg_color="#2563eb", text_color="#ffffff", hover_color="#1d4ed8")
            else:
                button.configure(fg_color="transparent", text_color="#cbd5e1", hover_color="#1e293b")

    def _rules_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="监控群名（每行一个）").grid(row=0, column=0, sticky="nw", pady=6)
        self.monitored_chats_text = tk.Text(frame, height=6, wrap="word")
        self.monitored_chats_text.grid(row=0, column=1, sticky="nsew", pady=6)
        self.monitored_chats_text.insert("1.0", "\n".join(self.config.rules.monitored_chats))

        ttk.Label(frame, text="我的 @ 名称").grid(row=1, column=0, sticky="nw", pady=6)
        self.my_names_text = tk.Text(frame, height=4, wrap="word")
        self.my_names_text.grid(row=1, column=1, sticky="nsew", pady=6)
        self.my_names_text.insert("1.0", "\n".join(self.config.rules.my_names))

        ttk.Label(frame, text="关键字").grid(row=2, column=0, sticky="nw", pady=6)
        self.keywords_text = tk.Text(frame, height=6, wrap="word")
        self.keywords_text.grid(row=2, column=1, sticky="nsew", pady=6)
        self.keywords_text.insert("1.0", "\n".join(self.config.rules.keywords))

        ttk.Checkbutton(
            frame,
            text="强关键字无群名也提醒",
            variable=self.allow_keyword_without_chat_var,
        ).grid(row=3, column=1, sticky="w", pady=8)
        return frame

    def _region_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)
        frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            frame,
            text="启用固定区域 OCR 轮询",
            variable=self.region_enabled_var,
        ).grid(row=0, column=1, sticky="w", pady=8)

        ttk.Label(frame, text="轮询间隔秒").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Spinbox(frame, from_=5, to=3600, increment=5, textvariable=self.region_interval_var, width=8).grid(
            row=1, column=1, sticky="w"
        )

        ttk.Label(frame, text="当前区域").grid(row=2, column=0, sticky="w", pady=8)
        ttk.Label(frame, textvariable=self.region_bbox_var).grid(row=2, column=1, sticky="w", pady=8)

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=3, column=1, sticky="w", pady=8)
        ctk.CTkButton(buttons, text="框选区域", height=34, command=self.select_region).pack(side="left")
        ctk.CTkButton(
            buttons,
            text="测试当前区域 OCR",
            height=34,
            fg_color="#e5e7eb",
            hover_color="#d1d5db",
            text_color="#111827",
            command=self.test_selected_region,
        ).pack(side="left", padx=8)

        ttk.Checkbutton(
            frame,
            text="区域 OCR 也要求命中监控群名",
            variable=self.region_require_chat_var,
        ).grid(row=4, column=1, sticky="w", pady=8)

        ttk.Checkbutton(
            frame,
            text="同时监控微信弹窗",
            variable=self.popup_ocr_enabled_var,
        ).grid(row=5, column=1, sticky="w", pady=8)

        ttk.Label(
            frame,
            text="区域模式适合把微信聊天窗口独立出来后固定扫描。默认只要区域文本出现 @ 名称或关键字就提醒。",
            wraplength=520,
            foreground="#555555",
        ).grid(row=6, column=1, sticky="w", pady=(12, 0))
        return frame

    def _notify_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="超时分钟").grid(row=0, column=0, sticky="w", pady=8)
        ttk.Spinbox(frame, from_=1, to=120, textvariable=self.escalation_var, width=8).grid(row=0, column=1, sticky="w")

        ttk.Label(frame, text="轮询间隔 ms").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Spinbox(frame, from_=100, to=3000, increment=100, textvariable=self.poll_interval_var, width=8).grid(
            row=1, column=1, sticky="w"
        )

        ttk.Label(frame, text="联系人").grid(row=2, column=0, sticky="w", pady=8)
        ttk.Entry(frame, textvariable=self.contact_name_var).grid(row=2, column=1, sticky="ew")

        ttk.Label(frame, text="通知方式").grid(row=3, column=0, sticky="w", pady=8)
        ttk.Label(frame, text="模拟点击通知").grid(row=3, column=1, sticky="w", pady=8)

        ttk.Label(frame, text="报警音文件").grid(row=4, column=0, sticky="w", pady=8)
        sound_row = ttk.Frame(frame)
        sound_row.grid(row=4, column=1, sticky="ew")
        sound_row.columnconfigure(0, weight=1)
        ttk.Entry(sound_row, textvariable=self.alarm_sound_var).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(sound_row, text="选择", width=76, height=30, command=self._select_sound).grid(
            row=0, column=1, padx=(8, 0)
        )

        ttk.Checkbutton(
            frame,
            text="调试模式：保存弹窗截图",
            variable=self.debug_screenshots_var,
        ).grid(row=5, column=1, sticky="w", pady=8)

        ttk.Label(frame, text="模拟点击输入框").grid(row=6, column=0, sticky="w", pady=8)
        automation_row = ttk.Frame(frame)
        automation_row.grid(row=6, column=1, sticky="ew", pady=8)
        ttk.Label(automation_row, textvariable=self.automation_pos_var).pack(side="left")
        ctk.CTkButton(automation_row, text="3秒后记录鼠标位置", height=32, command=self.record_mouse_position_later).pack(
            side="left", padx=8
        )

        ttk.Checkbutton(
            frame,
            text="粘贴后按 Enter 发送",
            variable=self.automation_press_enter_var,
        ).grid(row=7, column=1, sticky="w", pady=8)

        ttk.Label(frame, text="模拟通知模板").grid(row=8, column=0, sticky="nw", pady=8)
        self.automation_template_text = tk.Text(frame, height=6, wrap="word")
        self.automation_template_text.grid(row=8, column=1, sticky="nsew", pady=8)
        self.automation_template_text.insert("1.0", self.config.notification.automation_message_template)
        ttk.Label(
            frame,
            text="可用变量：{contact_name} {chat_name} {content} {raw_text} {time} {trigger_reason}",
            foreground="#555555",
        ).grid(row=9, column=1, sticky="w")
        return frame

    def _test_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="模拟 OCR 文本").grid(row=0, column=0, sticky="w")
        self.test_text = tk.Text(frame, height=9, wrap="word")
        self.test_text.grid(row=1, column=0, sticky="nsew", pady=6)
        self.test_text.insert("1.0", "运维值班群\n王五：@张三 线上报警了，看一下")

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", pady=8)
        ctk.CTkButton(buttons, text="测试规则并弹窗", height=34, command=self.test_rule_alert).pack(side="left")
        ctk.CTkButton(
            buttons,
            text="测试报警声音",
            height=34,
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            command=self.test_alarm,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            buttons,
            text="停止声音",
            height=34,
            fg_color="#e5e7eb",
            hover_color="#d1d5db",
            text_color="#111827",
            command=self.alarm.stop,
        ).pack(side="left")
        ctk.CTkButton(
            buttons,
            text="测试通知",
            height=34,
            fg_color="#0f766e",
            hover_color="#115e59",
            command=self.test_notification,
        ).pack(side="left", padx=8)
        return frame

    def _log_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(frame, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ctk.CTkButton(frame, text="刷新日志", width=92, height=32, command=self.refresh_logs).grid(
            row=1, column=0, sticky="e", pady=(8, 0)
        )
        self.refresh_logs()
        return frame

    def _select_sound(self) -> None:
        filename = filedialog.askopenfilename(
            title="选择报警音",
            filetypes=[
                ("常用音频", "*.mp3 *.ogg *.wav *.flac"),
                ("MP3", "*.mp3"),
                ("OGG", "*.ogg"),
                ("WAV", "*.wav"),
                ("FLAC", "*.flac"),
                ("所有文件", "*.*"),
            ],
        )
        if filename:
            self.alarm_sound_var.set(filename)

    def _refresh_dependency_status(self) -> None:
        missing = []
        try:
            import win32gui  # noqa: F401
            import win32process  # noqa: F401
            import psutil  # noqa: F401
        except Exception:
            self.logger.exception("窗口监控依赖检查失败")
            missing.append("真实窗口监控需要 pywin32、psutil")
        try:
            import PIL  # noqa: F401
        except Exception:
            self.logger.exception("截图依赖检查失败")
            missing.append("截图预处理需要 Pillow")
        ocr_failures = diagnose_ocr_imports(self.logger)
        if ocr_failures:
            missing.append("OCR 需要 paddleocr（详见日志）")
        if missing:
            self.dep_status_var.set("当前为可演示模式：" + "；".join(missing))
        else:
            self.dep_status_var.set("依赖完整：可进行真实微信弹窗 OCR 监控")

    def save_from_ui(self, show_message: bool = True) -> None:
        self.config.rules.monitored_chats = self._lines(self.monitored_chats_text)
        self.config.rules.my_names = self._lines(self.my_names_text)
        self.config.rules.keywords = self._lines(self.keywords_text)
        self.config.rules.allow_keyword_without_chat = self.allow_keyword_without_chat_var.get()
        self.config.app.escalation_minutes = int(self.escalation_var.get())
        self.config.app.poll_interval_ms = int(self.poll_interval_var.get())
        self.config.app.alarm_sound = self.alarm_sound_var.get().strip()
        self.config.app.debug_save_screenshots = self.debug_screenshots_var.get()
        self.config.wechat.enable_popup_ocr = self.popup_ocr_enabled_var.get()
        self.config.region.enabled = self.region_enabled_var.get()
        self.config.region.poll_interval_seconds = int(self.region_interval_var.get())
        self.config.region.require_chat_match = self.region_require_chat_var.get()
        self.config.notification.contact_name = self.contact_name_var.get().strip() or "联系人"
        self.config.notification.automation_press_enter = self.automation_press_enter_var.get()
        if self.automation_template_text is not None:
            self.config.notification.automation_message_template = self.automation_template_text.get("1.0", "end").strip()
        save_config(self.config)
        self.logger.info("配置已保存")
        if show_message:
            self._show_info("配置", "配置已保存")

    def start_monitoring(self) -> None:
        self.save_from_ui(show_message=False)
        if self.monitor:
            self.monitor.stop()
        self.monitor = WindowMonitor(
            self.config,
            self.logger,
            on_message=lambda message, reason: self.event_queue.put(("message", (message, reason))),
            on_status=lambda status: self.event_queue.put(("status", status)),
        )
        self.monitor.start()
        self.status_var.set("正在监控")

    def stop_monitoring(self) -> None:
        if self.monitor:
            self.monitor.stop()
        self.status_var.set("已停止")

    def test_rule_alert(self) -> None:
        self.save_from_ui(show_message=False)
        raw_text = self.test_text.get("1.0", "end").strip()
        message = parse_message(raw_text)
        result = match_rules(message, self.config.rules)
        if not result.matched:
            self._show_info("规则测试", "未命中规则")
            return
        self._create_alert(message, result.reason)

    def test_alarm(self) -> None:
        self.alarm.start(self.alarm_sound_var.get().strip())

    def test_notification(self) -> None:
        self.save_from_ui(show_message=False)
        message = parse_message(self.test_text.get("1.0", "end").strip())
        alert = Alert(message=message, trigger_reason="manual", escalation_minutes=self.config.app.escalation_minutes)
        self._send_notification_async(alert)

    def record_mouse_position_later(self) -> None:
        self._show_info("记录鼠标位置", "请在 3 秒内把鼠标移到联系人聊天窗口的输入框位置")

        def capture() -> None:
            try:
                import pyautogui
            except Exception as exc:  # noqa: BLE001
                self._show_error("模拟点击", f"缺少 pyautogui：{exc}")
                return
            pos = pyautogui.position()
            self.config.notification.automation_input_pos = [int(pos.x), int(pos.y)]
            self._update_automation_pos_label()
            self.save_from_ui(show_message=False)
            self._show_info("模拟点击", f"已记录输入框坐标：{pos.x}, {pos.y}")

        self.after(3000, capture)

    def select_region(self) -> None:
        self.iconify()

        def on_selected(bbox: list[int]) -> None:
            self.deiconify()
            self.config.region.bbox = bbox
            self.region_enabled_var.set(True)
            self.config.region.enabled = True
            self._update_region_label()
            self.save_from_ui(show_message=False)
            self.logger.info("已选择区域 bbox=%s", bbox)
            self._show_info("框选区域", "区域已保存，启动监控后会按间隔 OCR 扫描")

        self.after(300, lambda: RegionSelector(self, on_selected))

    def test_selected_region(self) -> None:
        self.save_from_ui(show_message=False)
        if not self.config.region.bbox:
            self._show_warning("区域监控", "请先框选区域")
            return
        monitor = WindowMonitor(
            self.config,
            self.logger,
            on_message=lambda message, reason: self.event_queue.put(("message", (message, reason))),
            on_status=lambda status: self.event_queue.put(("status", status)),
        )
        threading.Thread(target=monitor.process_region, daemon=True).start()

    def refresh_logs(self) -> None:
        path = Path("logs/app.log")
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", text[-12000:])

    def _update_region_label(self) -> None:
        bbox = self.config.region.bbox
        if len(bbox) == 4:
            left, top, right, bottom = bbox
            self.region_bbox_var.set(f"{left}, {top}, {right}, {bottom}  ({right - left} x {bottom - top})")
        else:
            self.region_bbox_var.set("未选择")

    def _update_automation_pos_label(self) -> None:
        pos = self.config.notification.automation_input_pos
        if len(pos) == 2:
            self.automation_pos_var.set(f"{pos[0]}, {pos[1]}")
        else:
            self.automation_pos_var.set("未记录")

    def _create_alert(self, message: Message, reason: str) -> None:
        alert = Alert(message=message, trigger_reason=reason, escalation_minutes=self.config.app.escalation_minutes)
        self.current_alerts[alert.id] = alert
        self.logger.info("开始强提醒 alert=%s reason=%s", alert.id, reason)
        self.alarm.start(self.config.app.alarm_sound)
        AlertWindow(self, alert, self.resolve_alert, self.delay_alert, self.notify_alert)
        self.after(1000, lambda: self._check_escalation(alert))

    def resolve_alert(self, alert: Alert) -> None:
        alert.resolve()
        self.alarm.stop()
        self.logger.info("用户已处理 alert=%s", alert.id)

    def delay_alert(self, alert: Alert) -> None:
        alert.delay(5)
        self.logger.info("用户延迟5分钟 alert=%s", alert.id)
        self._show_info("已延迟", "已延迟 5 分钟")

    def notify_alert(self, alert: Alert) -> None:
        self._send_notification_async(alert)

    def _check_escalation(self, alert: Alert) -> None:
        if alert.status not in ("pending", "delayed"):
            return
        if datetime.now() >= alert.deadline_at and not alert.notified_contact:
            self._send_notification_async(alert)
            return
        self.after(1000, lambda: self._check_escalation(alert))

    def _send_notification_async(self, alert: Alert) -> None:
        def run() -> None:
            self.event_queue.put(("notify_status", "通知状态：正在执行..."))
            result = send_notification(alert, self.config.notification, self.logger)
            self.event_queue.put(("notify_result", (alert, result.ok, result.message)))

        threading.Thread(target=run, daemon=True).start()

    def _process_events(self) -> None:
        while True:
            try:
                event, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if event == "message":
                message, reason = payload
                self._create_alert(message, reason)
            elif event == "status":
                self.status_var.set(str(payload))
            elif event == "notify_status":
                self.notify_status_var.set(str(payload))
            elif event == "notify_result":
                alert, ok, message = payload
                if ok:
                    alert.escalate()
                    self.logger.info("通知联系人成功 alert=%s message=%s", alert.id, message)
                    self.notify_status_var.set(f"通知状态：成功 - {message}")
                else:
                    alert.status = "failed"
                    self.logger.error("通知联系人失败 alert=%s message=%s", alert.id, message)
                    self.notify_status_var.set(f"通知状态：失败 - {message}")
                self.refresh_logs()
        self.after(200, self._process_events)

    def _show_info(self, title: str, message: str) -> None:
        show_topmost_message(self, "info", title, message)

    def _show_warning(self, title: str, message: str) -> None:
        show_topmost_message(self, "warning", title, message)

    def _show_error(self, title: str, message: str) -> None:
        show_topmost_message(self, "error", title, message)

    def _hide_window(self) -> None:
        self.iconify()

    @staticmethod
    def _lines(widget: tk.Text | None) -> list[str]:
        if widget is None:
            return []
        return [line.strip() for line in widget.get("1.0", "end").splitlines() if line.strip()]

    def destroy(self) -> None:
        self.stop_monitoring()
        self.alarm.stop()
        self.logger.info("软件退出")
        super().destroy()


def main() -> None:
    logger = setup_logging()
    config = load_config()
    app = MainWindow(config, logger)
    app.mainloop()

