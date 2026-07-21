from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

import flet as ft

from .alarm import AlarmPlayer
from .assets import APP_ICON_PNG, alarm_preset_options, ensure_app_icon_ico
from .config import Config, load_config, save_config
from .housekeeping import cleanup_runtime_files
from .keepalive import KeepAliveController
from .logging_setup import setup_logging
from .models import Alert, Message
from .monitor import AutoWindowOcrResult, WindowMonitor
from .notifier import NotifyResult, send_notification
from .rules import match_rules, parse_message
from .tray import TrayController
from .wechat_window import WeChatWindowLocator


BG = "#F5F7FB"
SURFACE = "#FFFFFF"
SIDEBAR = "#0F172A"
TEXT = "#111827"
MUTED = "#64748B"
PRIMARY = "#2563EB"
SUCCESS = "#059669"
DANGER = "#DC2626"
ICONS = ft.icons
FONT_FAMILY = "Microsoft YaHei"


def select_screen_region(
    bounds: tuple[int, int, int, int] | None = None,
    instruction: str = "按住鼠标左键拖拽框选监控区域，Esc 取消",
) -> list[int] | None:
    result: list[int] | None = None
    root = tk.Tk()
    root.withdraw()

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    if bounds:
        left, top, right, bottom = bounds
        left = max(0, int(left))
        top = max(0, int(top))
        right = min(screen_width, int(right))
        bottom = min(screen_height, int(bottom))
        if right <= left or bottom <= top:
            left, top, right, bottom = 0, 0, screen_width, screen_height
    else:
        left, top, right, bottom = 0, 0, screen_width, screen_height
    width = right - left
    height = bottom - top

    overlay = tk.Toplevel(root)
    overlay.overrideredirect(True)
    overlay.attributes("-topmost", True)
    overlay.attributes("-alpha", 0.28)
    overlay.configure(bg="black")
    overlay.geometry(f"{width}x{height}+{left}+{top}")

    canvas = tk.Canvas(overlay, cursor="crosshair", bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        24,
        24,
        anchor="nw",
        fill="white",
        font=(FONT_FAMILY, 16, "bold"),
        text=instruction,
    )

    state = {"x": 0, "y": 0, "rect": None}

    def start(event) -> None:
        state["x"] = event.x_root
        state["y"] = event.y_root
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#22C55E", width=3)

    def drag(event) -> None:
        if state["rect"]:
            canvas.coords(
                state["rect"],
                state["x"] - left,
                state["y"] - top,
                event.x_root - left,
                event.y_root - top,
            )

    def finish(event) -> None:
        nonlocal result
        selected_left = max(left, min(state["x"], event.x_root))
        selected_top = max(top, min(state["y"], event.y_root))
        selected_right = min(right, max(state["x"], event.x_root))
        selected_bottom = min(bottom, max(state["y"], event.y_root))
        if selected_right - selected_left >= 20 and selected_bottom - selected_top >= 20:
            result = [int(selected_left), int(selected_top), int(selected_right), int(selected_bottom)]
        overlay.destroy()

    canvas.bind("<ButtonPress-1>", start)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", finish)
    overlay.bind("<Escape>", lambda _event: overlay.destroy())
    overlay.focus_force()
    root.wait_window(overlay)
    root.destroy()
    return result


class FletAssistantApp:
    def __init__(self, page: ft.Page, config: Config, logger: logging.Logger):
        self.page = page
        self.config = config
        self.logger = logger
        self.alarm = AlarmPlayer(logger)
        self.keepalive = KeepAliveController(logger)
        self.monitor: WindowMonitor | None = None
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.current_alerts: dict[str, Alert] = {}
        self.current_view = "rules"
        self.running = True
        self.exit_requested = False
        self.tray = TrayController(logger, lambda action: self.event_queue.put(("tray", action)))

        self._build_fields()
        self._build_page()
        self._refresh_dependency_status()
        self._apply_keep_awake()
        if self.config.app.enable_tray:
            self.tray.start()
        self.page.run_thread(self._pump_events)

    def _build_fields(self) -> None:
        self.status_text = ft.Text("未启动", size=12, color="#CBD5E1")
        self.dep_status = ft.Text("", size=12, color="#92400E")
        self.notify_status = ft.Text("通知状态：未执行", size=12, color=MUTED)

        self.monitored_chats = self._text_area("\n".join(self.config.rules.monitored_chats), min_lines=5, max_lines=8)
        self.my_names = self._text_area("\n".join(self.config.rules.my_names), min_lines=3, max_lines=5)
        self.keywords = self._text_area("\n".join(self.config.rules.keywords), min_lines=5, max_lines=8)
        self.allow_keyword_without_chat = ft.Switch(
            label="无群名也按关键字提醒",
            value=self.config.rules.allow_keyword_without_chat,
            active_color=PRIMARY,
        )
        self.fuzzy_name_threshold = self._field(
            str(self.config.rules.fuzzy_name_threshold),
            label="@ 名称相似度",
        )

        self.auto_window_enabled = ft.Switch(
            label="开启自动窗口监控",
            value=self.config.auto_window_ocr.enabled,
            active_color=PRIMARY,
        )
        self.auto_window_interval = self._field(
            str(self.config.auto_window_ocr.poll_interval_seconds),
            label="识别间隔（秒）",
        )
        selected_window_value = self._window_select_value(
            self.config.auto_window_ocr.selected_window_hwnd,
            self.config.auto_window_ocr.selected_window_title,
        )
        self.auto_window_select = ft.Dropdown(
            label="监控窗口",
            value=selected_window_value,
            options=[
                ft.dropdown.Option(
                    key=selected_window_value,
                    text=self.config.auto_window_ocr.selected_window_title,
                )
            ]
            if selected_window_value
            else [],
            border_radius=10,
        )
        self.auto_crop_left = self._field(str(self.config.auto_window_ocr.crop_left), label="左")
        self.auto_crop_top = self._field(str(self.config.auto_window_ocr.crop_top), label="上")
        self.auto_crop_right = self._field(str(self.config.auto_window_ocr.crop_right), label="右")
        self.auto_crop_bottom = self._field(str(self.config.auto_window_ocr.crop_bottom), label="下")
        self.auto_recent_lines = self._field(
            str(self.config.auto_window_ocr.max_recent_lines),
            label="末尾行数",
        )
        self.auto_dedup_seconds = self._field(
            str(self.config.auto_window_ocr.dedup_seconds),
            label="静默秒数",
        )
        self.auto_dedup_similarity = self._field(
            str(self.config.auto_window_ocr.dedup_similarity),
            label="重复相似度",
        )
        self.cross_source_dedup_seconds = self._field(
            str(self.config.app.cross_source_dedup_seconds),
            label="跨模式合并秒数",
        )
        self.auto_skip_existing = ft.Checkbox(
            label="忽略启动前旧消息",
            value=self.config.auto_window_ocr.skip_existing_on_start,
        )
        self.auto_require_chat = ft.Checkbox(
            label="要求命中群名",
            value=self.config.auto_window_ocr.require_chat_match,
        )
        self.auto_probe_status = ft.Text("尚未测试", size=13, color=MUTED, selectable=True)
        self.auto_probe_text = self._text_area("", min_lines=7, max_lines=10)
        self.auto_probe_text.read_only = True
        self.auto_preview = ft.Image(width=760, height=260, fit=ft.ImageFit.CONTAIN, visible=False)

        self.region_enabled = ft.Switch(
            label="开启固定区域监控",
            value=self.config.region.enabled,
            active_color=PRIMARY,
        )
        self.region_interval = self._field(
            str(self.config.region.poll_interval_seconds),
            label="识别间隔（秒）",
        )
        self.region_recent_lines = self._field(
            str(self.config.region.max_recent_lines),
            label="末尾行数",
        )
        self.region_dedup_seconds = self._field(
            str(self.config.region.dedup_seconds),
            label="静默秒数",
        )
        self.region_dedup_similarity = self._field(
            str(self.config.region.dedup_similarity),
            label="重复相似度",
        )
        self.region_skip_existing = ft.Checkbox(
            label="忽略启动前旧消息",
            value=self.config.region.skip_existing_on_start,
        )
        self.region_require_chat = ft.Checkbox(
            label="要求命中群名",
            value=self.config.region.require_chat_match,
        )
        self.popup_ocr_enabled = ft.Checkbox(
            label="旧版弹窗监控",
            value=self.config.wechat.enable_popup_ocr,
        )
        self.region_bbox = ft.Text(self._format_bbox(), size=13, color=TEXT)

        self.escalation_minutes = self._field(str(self.config.app.escalation_minutes), label="超时分钟")
        self.poll_interval = self._field(str(self.config.app.poll_interval_ms), label="弹窗检查 ms")
        self.keep_awake_enabled = ft.Switch(
            label="开启保活",
            value=self.config.app.keep_awake_enabled,
            active_color=PRIMARY,
        )
        self.keep_awake_interval = self._field(
            str(self.config.app.keep_awake_interval_seconds),
            label="保活间隔（秒）",
        )
        self.keep_awake_simulate_input = ft.Checkbox(
            label="F15 刷新",
            value=self.config.app.keep_awake_simulate_input,
        )
        self.keep_awake_mouse_nudge = ft.Checkbox(
            label="鼠标微动",
            value=self.config.app.keep_awake_mouse_nudge,
        )
        self.contact_name = self._field(self.config.notification.contact_name, label="联系人")
        self.notification_retry_attempts = self._field(
            str(self.config.notification.retry_attempts),
            label="重试次数",
        )
        self.notification_retry_delay = self._field(
            str(self.config.notification.retry_delay_seconds),
            label="重试间隔（秒）",
        )
        self.alarm_preset = ft.Dropdown(
            label="报警音",
            value=self._alarm_preset_value(self.config.app.alarm_sound),
            options=[
                *[
                    ft.dropdown.Option(key=key, text=label)
                    for key, label in alarm_preset_options()
                ],
                ft.dropdown.Option(key="custom", text="导入音频文件"),
            ],
            border_radius=10,
        )
        self.alarm_sound = self._field(
            "" if self.config.app.alarm_sound.startswith("preset:") else self.config.app.alarm_sound,
            label="自定义文件",
        )
        self.debug_screenshots = ft.Checkbox(
            label="保存截图",
            value=self.config.app.debug_save_screenshots,
        )
        self.enable_tray = ft.Checkbox(
            label="启用系统托盘",
            value=self.config.app.enable_tray,
        )
        self.cleanup_screenshots_keep = self._field(
            str(self.config.app.cleanup_screenshots_keep),
            label="截图保留",
        )
        self.automation_pos = ft.Text(self._format_automation_pos(), size=13, color=TEXT)
        self.automation_press_enter = ft.Checkbox(
            label="Enter 发送",
            value=self.config.notification.automation_press_enter,
        )
        self.automation_template = self._text_area(
            self.config.notification.automation_message_template,
            min_lines=5,
            max_lines=7,
        )

        self.test_text = self._text_area("运维值班群\n王五：@张三 线上报警了，看一下", min_lines=7, max_lines=10)
        self.log_text = ft.TextField(
            value="",
            multiline=True,
            read_only=True,
            expand=True,
            border_radius=12,
            text_size=12,
            min_lines=22,
            max_lines=22,
        )

    def _build_page(self) -> None:
        self.page.title = "微信强提醒助手"
        self.page.bgcolor = BG
        self.page.padding = 0
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.theme = ft.Theme(font_family=FONT_FAMILY, use_material3=True)
        self.page.window_width = 1600
        self.page.window_height = 900
        self.page.window_min_width = 980
        self.page.window_min_height = 680
        self.page.window_prevent_close = bool(self.config.app.enable_tray)

        self.file_picker = ft.FilePicker(on_result=self._on_sound_selected)
        self.page.overlay.append(self.file_picker)

        self.nav_items = {
            "rules": self._nav_item("规则配置", ICONS.RULE, "rules"),
            "monitor": self._nav_item("监控设置", ICONS.CENTER_FOCUS_STRONG, "monitor"),
            "notify": self._nav_item("通知报警", ICONS.NOTIFICATIONS_ACTIVE, "notify"),
            "test": self._nav_item("测试工具", ICONS.SCIENCE, "test"),
            "logs": self._nav_item("运行日志", ICONS.ARTICLE, "logs"),
        }

        self.content_host = ft.Container(expand=True)
        self._set_view("rules", update=False)

        self.page.add(
            ft.Row(
                controls=[
                    self._sidebar(),
                    ft.Container(
                        expand=True,
                        padding=ft.padding.only(left=24, top=22, right=24, bottom=22),
                        content=ft.Column(
                            spacing=16,
                            expand=True,
                            controls=[
                                self._top_bar(),
                                self.content_host,
                            ],
                        ),
                    ),
                ],
                spacing=0,
                expand=True,
            )
        )
        self.refresh_logs()
        self.page.run_thread(self._apply_window_icon)

    def _apply_window_icon(self) -> None:
        icon_path = ensure_app_icon_ico()
        if not icon_path:
            self.logger.warning("应用图标不存在或转换失败：%s", APP_ICON_PNG)
            return
        try:
            import win32con
            import win32gui
            import win32process
        except Exception as exc:  # noqa: BLE001
            self.logger.info("窗口图标设置跳过，缺少 pywin32：%s", exc)
            return

        try:
            target_pid = os.getpid()
            hwnds: list[int] = []

            def callback(hwnd, _extra):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    pid = 0
                title = win32gui.GetWindowText(hwnd) or ""
                if "微信强提醒助手" in title or (pid == target_pid and title):
                    hwnds.append(hwnd)

            for _attempt in range(20):
                hwnds.clear()
                win32gui.EnumWindows(callback, None)
                if hwnds:
                    break
                time.sleep(0.5)
            if not hwnds:
                self.logger.info("未找到可设置图标的主窗口")
                return
            icon_handle = win32gui.LoadImage(
                None,
                str(icon_path),
                win32con.IMAGE_ICON,
                0,
                0,
                win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
            )
            small_icon = win32gui.LoadImage(
                None,
                str(icon_path),
                win32con.IMAGE_ICON,
                16,
                16,
                win32con.LR_LOADFROMFILE,
            )
            for hwnd in hwnds:
                win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_BIG, icon_handle)
                win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_SMALL, small_icon)
            self.logger.info("窗口图标已设置：%s", icon_path)
        except Exception:
            self.logger.exception("窗口图标设置失败")

    def _sidebar(self) -> ft.Control:
        return ft.Container(
            width=226,
            bgcolor=SIDEBAR,
            padding=ft.padding.symmetric(horizontal=14, vertical=18),
            content=ft.Column(
                expand=True,
                controls=[
                    ft.Container(
                        padding=ft.padding.only(left=10, top=4, bottom=18),
                        content=ft.Column(
                            spacing=2,
                            controls=[
                                ft.Text("微信强提醒", size=20, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                                ft.Text("Flet 桌面版", size=12, color="#94A3B8"),
                            ],
                        ),
                    ),
                    *self.nav_items.values(),
                    ft.Container(expand=True),
                    ft.Container(
                        bgcolor="#111827",
                        border_radius=14,
                        padding=14,
                        content=ft.Column(
                            spacing=4,
                            controls=[
                                ft.Text("运行状态", size=12, color="#94A3B8"),
                                self.status_text,
                            ],
                        ),
                    ),
                ],
            ),
        )

    def _top_bar(self) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Column(
                        spacing=4,
                        controls=[
                            ft.Text("微信强提醒助手", size=26, weight=ft.FontWeight.BOLD, color=TEXT),
                            self.dep_status,
                            self.notify_status,
                        ],
                    ),
                    ft.Row(
                        spacing=10,
                        controls=[
                            self._button("保存配置", ICONS.SAVE, self.save_from_ui),
                            self._button("启动监控", ICONS.PLAY_ARROW, self.start_monitoring, bgcolor=SUCCESS),
                            self._button(
                                "停止监控",
                                ICONS.STOP,
                                self.stop_monitoring,
                                bgcolor="#E5E7EB",
                                color=TEXT,
                            ),
                        ],
                    ),
                ],
            )
        )

    def _rules_view(self) -> ft.Control:
        return self._panel(
            "规则配置",
            "设置触发提醒的群、名称和关键字。",
            ft.Row(
                spacing=22,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Container(
                        width=520,
                        content=ft.Column(
                            spacing=18,
                            controls=[
                                self._settings_section(
                                    "监控对象",
                                    "每行一个群名。",
                                    [
                                        self.monitored_chats,
                                    ],
                                ),
                                self._settings_section(
                                    "@ 名称",
                                    "每行一个称呼。",
                                    [
                                        self.my_names,
                                    ],
                                ),
                            ],
                        ),
                    ),
                    ft.Container(
                        width=560,
                        content=ft.Column(
                            spacing=18,
                            controls=[
                                self._settings_section(
                                    "关键字",
                                    "每行一个词。",
                                    [
                                        self.keywords,
                                    ],
                                ),
                                self._settings_section(
                                    "匹配选项",
                                    "相似度推荐 0.88；越高越严格。",
                                    [
                                        ft.Row(
                                            spacing=16,
                                            wrap=True,
                                            controls=[
                                                self.allow_keyword_without_chat,
                                                ft.Container(width=240, content=self.fuzzy_name_threshold),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )

    def _monitor_view(self) -> ft.Control:
        return self._panel(
            "监控设置",
            "自动窗口为主，固定区域兜底。",
            ft.Tabs(
                selected_index=0,
                animation_duration=180,
                tabs=[
                    ft.Tab(text="自动窗口 OCR", icon=ICONS.CENTER_FOCUS_STRONG, content=self._auto_window_content()),
                    ft.Tab(text="固定区域 OCR", icon=ICONS.CROP_FREE, content=self._region_content()),
                ],
            ),
        )

    def _auto_window_content(self) -> ft.Control:
        return ft.Container(
            padding=ft.padding.only(top=16),
            content=ft.Column(
                spacing=18,
                controls=[
                    ft.Row(
                        spacing=22,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                width=540,
                                content=ft.Column(
                                    spacing=18,
                                    controls=[
                                        self._settings_section(
                                            "窗口来源",
                                            "选择当前微信聊天窗口。",
                                            [
                                                self.auto_window_enabled,
                                                ft.Row(
                                                    spacing=10,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=390, content=self.auto_window_select),
                                                        self._button(
                                                            "刷新",
                                                            ICONS.REFRESH,
                                                            self.refresh_wechat_windows,
                                                            bgcolor="#E5E7EB",
                                                            color=TEXT,
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                        self._settings_section(
                                            "识别与去重",
                                            "推荐：间隔 3-10 秒，末尾 12 行。",
                                            [
                                                ft.Row(
                                                    spacing=12,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=220, content=self.auto_window_interval),
                                                        ft.Container(width=220, content=self.auto_recent_lines),
                                                        ft.Container(width=220, content=self.auto_dedup_seconds),
                                                        ft.Container(width=220, content=self.auto_dedup_similarity),
                                                        ft.Container(width=220, content=self.cross_source_dedup_seconds),
                                                    ],
                                                ),
                                                ft.Row(
                                                    spacing=16,
                                                    wrap=True,
                                                    controls=[
                                                        self.auto_skip_existing,
                                                        self.auto_require_chat,
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ),
                            ft.Container(
                                width=560,
                                content=ft.Column(
                                    spacing=18,
                                    controls=[
                                        self._settings_section(
                                            "消息区裁剪",
                                            "单位像素；建议直接框选消息列表。",
                                            [
                                                ft.Row(
                                                    spacing=12,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=120, content=self.auto_crop_left),
                                                        ft.Container(width=120, content=self.auto_crop_top),
                                                        ft.Container(width=120, content=self.auto_crop_right),
                                                        ft.Container(width=120, content=self.auto_crop_bottom),
                                                    ],
                                                ),
                                                ft.Row(
                                                    spacing=10,
                                                    wrap=True,
                                                    controls=[
                                                        self._button(
                                                            "预览",
                                                            ICONS.IMAGE_SEARCH,
                                                            self.preview_auto_window,
                                                            bgcolor="#2563EB",
                                                        ),
                                                        self._button(
                                                            "框选消息区",
                                                            ICONS.CROP_FREE,
                                                            self.select_auto_window_message_area,
                                                            bgcolor="#059669",
                                                        ),
                                                        self._button(
                                                            "测试 OCR",
                                                            ICONS.DOCUMENT_SCANNER,
                                                            self.test_auto_window_ocr,
                                                            bgcolor="#7C3AED",
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                        self._settings_section(
                                            "测试结果",
                                            "窗口、裁剪和命中状态。",
                                            [
                                                self.auto_probe_status,
                                                self.auto_preview,
                                            ],
                                        ),
                                    ],
                                ),
                            ),
                        ],
                    ),
                    self._settings_section(
                        "OCR 调试信息",
                        "原始 OCR、清洗文本、新增行和命中项。",
                        [
                            self.auto_probe_text,
                        ],
                    ),
                ],
            ),
        )

    def _region_content(self) -> ft.Control:
        return ft.Container(
            padding=ft.padding.only(top=16),
            content=ft.Column(
                spacing=18,
                controls=[
                    ft.Row(
                        spacing=22,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                width=540,
                                content=ft.Column(
                                    spacing=18,
                                    controls=[
                                        self._settings_section(
                                            "区域来源",
                                            "自动窗口不稳定时使用。",
                                            [
                                                self.region_enabled,
                                                ft.Row(
                                                    spacing=10,
                                                    wrap=True,
                                                    controls=[
                                                        self._button("框选区域", ICONS.CROP_FREE, self.select_region),
                                                        self._button(
                                                            "测试 OCR",
                                                            ICONS.DOCUMENT_SCANNER,
                                                            self.test_selected_region,
                                                            bgcolor="#E5E7EB",
                                                            color=TEXT,
                                                        ),
                                                    ],
                                                ),
                                                ft.Container(
                                                    width=340,
                                                    padding=ft.padding.symmetric(vertical=4),
                                                    content=ft.Column(
                                                        spacing=3,
                                                        controls=[
                                                            ft.Text("当前区域", size=12, color=MUTED),
                                                            self.region_bbox,
                                                        ],
                                                    ),
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ),
                            ft.Container(
                                width=560,
                                content=ft.Column(
                                    spacing=18,
                                    controls=[
                                        self._settings_section(
                                            "识别与去重",
                                            "推荐：间隔 3-10 秒，末尾 12 行。",
                                            [
                                                ft.Row(
                                                    spacing=12,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=220, content=self.region_interval),
                                                        ft.Container(width=220, content=self.region_recent_lines),
                                                        ft.Container(width=220, content=self.region_dedup_seconds),
                                                        ft.Container(width=220, content=self.region_dedup_similarity),
                                                    ],
                                                ),
                                                ft.Row(
                                                    spacing=16,
                                                    wrap=True,
                                                    controls=[
                                                        self.region_skip_existing,
                                                        self.region_require_chat,
                                                        self.popup_ocr_enabled,
                                                    ],
                                                ),
                                            ],
                                        ),
                                        ft.Text(
                                            "默认按 @ 名称和关键字提醒；可要求先命中群名。",
                                            size=12,
                                            color=MUTED,
                                        ),
                                    ],
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        )

    def _notify_view(self) -> ft.Control:
        return self._panel(
            "通知与报警",
            "声音、保活和超时通知。",
            ft.Column(
                spacing=18,
                controls=[
                    ft.Row(
                        spacing=18,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                width=520,
                                content=ft.Column(
                                    spacing=18,
                                    controls=[
                                        self._settings_section(
                                            "基础升级",
                                            "超时后通知指定联系人。",
                                            [
                                                ft.Row(
                                                    spacing=12,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=170, content=self.escalation_minutes),
                                                        ft.Container(width=280, content=self.contact_name),
                                                    ],
                                                ),
                                            ],
                                        ),
                                        self._settings_section(
                                            "报警声音",
                                            "选择预设，或导入本地音频。",
                                            [
                                                ft.Row(
                                                    spacing=10,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=190, content=self.alarm_preset),
                                                        ft.Container(width=180, content=self.alarm_sound),
                                                        self._button(
                                                            "导入",
                                                            ICONS.AUDIO_FILE,
                                                            self.pick_alarm_sound,
                                                            bgcolor="#E5E7EB",
                                                            color=TEXT,
                                                        ),
                                                    ],
                                                ),
                                            ],
                                        ),
                                        self._settings_section(
                                            "运行选项",
                                            "调试截图、托盘和旧版弹窗检查。",
                                            [
                                                ft.Row(
                                                    spacing=12,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=210, content=self.poll_interval),
                                                        ft.Container(width=220, content=self.cleanup_screenshots_keep),
                                                    ],
                                                ),
                                                ft.Row(
                                                    spacing=18,
                                                    wrap=True,
                                                    controls=[
                                                        self.debug_screenshots,
                                                        self.enable_tray,
                                                    ],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ),
                            ft.Container(
                                width=560,
                                content=ft.Column(
                                    spacing=18,
                                    controls=[
                                        self._settings_section(
                                            "电脑保活",
                                            "防睡眠、熄屏和空闲锁屏。",
                                            [
                                                self.keep_awake_enabled,
                                                ft.Row(
                                                    spacing=12,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=210, content=self.keep_awake_interval),
                                                        self.keep_awake_simulate_input,
                                                        self.keep_awake_mouse_nudge,
                                                    ],
                                                ),
                                                ft.Text(
                                                    "F15 不输入文字；鼠标微动会原地往返。",
                                                    size=12,
                                                    color=MUTED,
                                                ),
                                            ],
                                        ),
                                        self._settings_section(
                                            "模拟点击通知",
                                            "点击坐标，粘贴模板。",
                                            [
                                                ft.Row(
                                                    spacing=12,
                                                    wrap=True,
                                                    controls=[
                                                        ft.Container(width=220, content=self.notification_retry_attempts),
                                                        ft.Container(width=220, content=self.notification_retry_delay),
                                                    ],
                                                ),
                                                ft.Row(
                                                    spacing=10,
                                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                    controls=[
                                                        ft.Container(
                                                            width=250,
                                                            padding=ft.padding.symmetric(vertical=4),
                                                            content=ft.Column(
                                                                spacing=3,
                                                                controls=[
                                                                    ft.Text("输入框坐标", size=12, color=MUTED),
                                                                    self.automation_pos,
                                                                ],
                                                            ),
                                                        ),
                                                        self._button(
                                                            "记录坐标",
                                                            ICONS.MY_LOCATION,
                                                            self.record_mouse_position_later,
                                                            bgcolor="#E5E7EB",
                                                            color=TEXT,
                                                        ),
                                                    ],
                                                ),
                                                self.automation_press_enter,
                                                self._label("模拟通知模板"),
                                                self.automation_template,
                                                ft.Text(
                                                    "变量：{contact_name} {chat_name} {content} {time}",
                                                    size=12,
                                                    color=MUTED,
                                                    no_wrap=False,
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        )

    def _test_view(self) -> ft.Control:
        return self._panel(
            "测试工具",
            "验证规则、声音和通知。",
            ft.Row(
                spacing=22,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Container(
                        width=620,
                        content=self._settings_section(
                            "模拟 OCR 文本",
                            "输入识别结果，测试规则。",
                            [
                                self.test_text,
                            ],
                        ),
                    ),
                    ft.Container(
                        width=420,
                        content=self._settings_section(
                            "测试动作",
                            "通知测试会真实点击。",
                            [
                                ft.Row(
                                    spacing=10,
                                    wrap=True,
                                    controls=[
                                        self._button("规则弹窗", ICONS.WARNING, self.test_rule_alert),
                                        self._button("报警声音", ICONS.VOLUME_UP, self.test_alarm, bgcolor="#7C3AED"),
                                        self._button(
                                            "停止声音",
                                            ICONS.VOLUME_OFF,
                                            lambda _=None: self.alarm.stop(),
                                            bgcolor="#E5E7EB",
                                            color=TEXT,
                                        ),
                                        self._button("测试通知", ICONS.SEND, self.test_notification, bgcolor="#0F766E"),
                                    ],
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )

    def _logs_view(self) -> ft.Control:
        return self._panel(
            "运行日志",
            "查看启动、OCR、命中和通知记录。",
            ft.Column(
                spacing=14,
                expand=True,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.END,
                        spacing=10,
                        wrap=True,
                        controls=[
                            self._button(
                                "清理文件",
                                ICONS.CLEANING_SERVICES,
                                self.cleanup_files,
                                bgcolor="#E5E7EB",
                                color=TEXT,
                            ),
                            self._button("刷新日志", ICONS.REFRESH, self.refresh_logs),
                        ],
                    ),
                    self.log_text,
                ],
            ),
        )

    def _panel(self, title: str, subtitle: str, content: ft.Control) -> ft.Control:
        return ft.Container(
            expand=True,
            bgcolor=SURFACE,
            border_radius=20,
            padding=24,
            shadow=ft.BoxShadow(blur_radius=22, spread_radius=0, color="#14000000", offset=ft.Offset(0, 8)),
            content=ft.Column(
                spacing=16,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Column(
                        spacing=3,
                        controls=[
                            ft.Text(title, size=20, weight=ft.FontWeight.BOLD, color=TEXT),
                            ft.Text(subtitle, size=12, color=MUTED, no_wrap=False),
                        ],
                    ),
                    ft.Divider(height=1, color="#E5E7EB"),
                    content,
                ],
            ),
        )

    def _settings_section(self, title: str, subtitle: str, controls: list[ft.Control]) -> ft.Control:
        return ft.Column(
            spacing=10,
            controls=[
                ft.Column(
                    spacing=2,
                    controls=[
                        ft.Text(title, size=15, weight=ft.FontWeight.BOLD, color=TEXT),
                        ft.Text(subtitle, size=12, color=MUTED, no_wrap=False),
                    ],
                ),
                *controls,
                ft.Divider(height=1, color="#EEF2F7"),
            ],
        )

    def _set_view(self, key: str, update: bool = True) -> None:
        self.current_view = key
        views = {
            "rules": self._rules_view,
            "monitor": self._monitor_view,
            "notify": self._notify_view,
            "test": self._test_view,
            "logs": self._logs_view,
        }
        self.content_host.content = views[key]()
        for nav_key, item in getattr(self, "nav_items", {}).items():
            item.bgcolor = PRIMARY if nav_key == key else None
            item.content.controls[0].color = "#FFFFFF" if nav_key == key else "#CBD5E1"
            item.content.controls[1].color = "#FFFFFF" if nav_key == key else "#CBD5E1"
        if update:
            self.page.update()

    def _nav_item(self, label: str, icon: str, key: str) -> ft.Container:
        return ft.Container(
            border_radius=12,
            ink=True,
            padding=ft.padding.symmetric(horizontal=12, vertical=11),
            on_click=lambda _event: self._set_view(key),
            content=ft.Row(
                spacing=10,
                controls=[
                    ft.Icon(icon, size=20, color="#CBD5E1"),
                    ft.Text(label, size=14, color="#CBD5E1"),
                ],
            ),
        )

    def _button(
        self,
        text: str,
        icon: str | None,
        handler: Callable,
        bgcolor: str = PRIMARY,
        color: str = "#FFFFFF",
    ) -> ft.Control:
        return ft.FilledButton(
            text=text,
            icon=icon,
            style=ft.ButtonStyle(
                bgcolor=bgcolor,
                color=color,
                text_style=ft.TextStyle(font_family=FONT_FAMILY),
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.padding.symmetric(14, 10),
            ),
            on_click=handler,
        )

    def _field(
        self,
        value: str,
        label: str,
        password: bool = False,
        helper_text: str | None = None,
    ) -> ft.TextField:
        return ft.TextField(
            value=value,
            label=label,
            password=password,
            can_reveal_password=password,
            helper_text=helper_text,
            helper_style=ft.TextStyle(size=11, color=MUTED, font_family=FONT_FAMILY),
            border_radius=12,
            filled=True,
            fill_color="#F8FAFC",
            border_color="#E5E7EB",
            focused_border_color=PRIMARY,
            text_style=ft.TextStyle(font_family=FONT_FAMILY),
            label_style=ft.TextStyle(font_family=FONT_FAMILY),
            content_padding=ft.padding.symmetric(14, 12),
        )

    def _text_area(self, value: str, min_lines: int, max_lines: int) -> ft.TextField:
        return ft.TextField(
            value=value,
            multiline=True,
            min_lines=min_lines,
            max_lines=max_lines,
            border_radius=12,
            filled=True,
            fill_color="#F8FAFC",
            border_color="#E5E7EB",
            focused_border_color=PRIMARY,
            text_style=ft.TextStyle(font_family=FONT_FAMILY),
            content_padding=ft.padding.all(14),
        )

    def _label(self, text: str) -> ft.Text:
        return ft.Text(text, size=13, color=TEXT, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY)

    def _refresh_dependency_status(self) -> None:
        missing = []
        try:
            import win32gui  # noqa: F401
            import win32process  # noqa: F401
            import psutil  # noqa: F401
        except Exception:
            missing.append("窗口监控依赖")
        try:
            import PIL  # noqa: F401
        except Exception:
            missing.append("截图依赖")
        try:
            import paddleocr  # noqa: F401
        except Exception:
            missing.append("OCR 依赖")
        if missing:
            self.dep_status.value = "缺少：" + "、".join(missing)
        else:
            self.dep_status.value = "依赖完整"

    def _apply_keep_awake(self) -> None:
        if self.config.app.keep_awake_enabled:
            self.keepalive.start(
                self.config.app.keep_awake_interval_seconds,
                self.config.app.keep_awake_simulate_input,
                self.config.app.keep_awake_mouse_nudge,
            )
        else:
            self.keepalive.stop()

    def save_from_ui(self, _event=None, show_message: bool = True) -> None:
        self.config.rules.monitored_chats = self._lines(self.monitored_chats.value)
        self.config.rules.my_names = self._lines(self.my_names.value)
        self.config.rules.keywords = self._lines(self.keywords.value)
        self.config.rules.allow_keyword_without_chat = bool(self.allow_keyword_without_chat.value)
        self.config.rules.fuzzy_name_threshold = self._threshold_value(
            self.fuzzy_name_threshold.value,
            0.88,
        )
        self.config.app.escalation_minutes = self._int_value(self.escalation_minutes.value, 3)
        self.config.app.poll_interval_ms = self._int_value(self.poll_interval.value, 300)
        self.config.app.keep_awake_enabled = bool(self.keep_awake_enabled.value)
        self.config.app.keep_awake_interval_seconds = max(30, self._int_value(self.keep_awake_interval.value, 240))
        self.config.app.keep_awake_simulate_input = bool(self.keep_awake_simulate_input.value)
        self.config.app.keep_awake_mouse_nudge = bool(self.keep_awake_mouse_nudge.value)
        self.config.app.alarm_sound = self._selected_alarm_sound()
        self.config.app.debug_save_screenshots = bool(self.debug_screenshots.value)
        self.config.app.cleanup_screenshots_keep = max(0, self._int_value(self.cleanup_screenshots_keep.value, 80))
        self.config.app.enable_tray = bool(self.enable_tray.value)
        self.config.app.cross_source_dedup_seconds = max(
            1,
            self._int_value(self.cross_source_dedup_seconds.value, 12),
        )
        self.config.wechat.enable_popup_ocr = bool(self.popup_ocr_enabled.value)
        self.config.auto_window_ocr.enabled = bool(self.auto_window_enabled.value)
        self._save_selected_auto_window()
        self.config.auto_window_ocr.poll_interval_seconds = self._int_value(self.auto_window_interval.value, 3)
        self.config.auto_window_ocr.crop_left = self._int_value(self.auto_crop_left.value, 300)
        self.config.auto_window_ocr.crop_top = self._int_value(self.auto_crop_top.value, 80)
        self.config.auto_window_ocr.crop_right = self._int_value(self.auto_crop_right.value, 20)
        self.config.auto_window_ocr.crop_bottom = self._int_value(self.auto_crop_bottom.value, 160)
        self.config.auto_window_ocr.require_chat_match = bool(self.auto_require_chat.value)
        self.config.auto_window_ocr.max_recent_lines = self._int_value(self.auto_recent_lines.value, 12)
        self.config.auto_window_ocr.dedup_seconds = self._int_value(self.auto_dedup_seconds.value, 180)
        self.config.auto_window_ocr.dedup_similarity = self._threshold_value(
            self.auto_dedup_similarity.value,
            0.88,
        )
        self.config.auto_window_ocr.skip_existing_on_start = bool(self.auto_skip_existing.value)
        self.config.region.enabled = bool(self.region_enabled.value)
        self.config.region.poll_interval_seconds = self._int_value(self.region_interval.value, 30)
        self.config.region.require_chat_match = bool(self.region_require_chat.value)
        self.config.region.max_recent_lines = self._int_value(self.region_recent_lines.value, 12)
        self.config.region.dedup_seconds = self._int_value(self.region_dedup_seconds.value, 180)
        self.config.region.dedup_similarity = self._threshold_value(
            self.region_dedup_similarity.value,
            0.88,
        )
        self.config.region.skip_existing_on_start = bool(self.region_skip_existing.value)
        self.config.notification.contact_name = self.contact_name.value.strip() or "联系人"
        self.config.notification.retry_attempts = max(0, self._int_value(self.notification_retry_attempts.value, 2))
        self.config.notification.retry_delay_seconds = max(1, self._int_value(self.notification_retry_delay.value, 60))
        self.config.notification.automation_press_enter = bool(self.automation_press_enter.value)
        self.config.notification.automation_message_template = self.automation_template.value.strip()
        save_config(self.config)
        self._apply_keep_awake()
        self.logger.info("配置已保存")
        if show_message:
            self._snack("配置已保存")

    def start_monitoring(self, _event=None) -> None:
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
        self.status_text.value = "正在监控"
        self._snack("监控已启动")
        self.page.update()

    def stop_monitoring(self, _event=None, show_message: bool = True) -> None:
        if self.monitor:
            self.monitor.stop()
        self.status_text.value = "已停止"
        if show_message:
            self._snack("监控已停止")
        self.page.update()

    def pick_alarm_sound(self, _event=None) -> None:
        self.file_picker.pick_files(
            dialog_title="选择报警音",
            allowed_extensions=["mp3", "ogg", "wav", "flac"],
            allow_multiple=False,
        )

    def _on_sound_selected(self, event: ft.FilePickerResultEvent) -> None:
        if event.files:
            self.alarm_preset.value = "custom"
            self.alarm_sound.value = event.files[0].path or event.files[0].name
            self.page.update()

    def refresh_wechat_windows(self, _event=None) -> None:
        self.auto_probe_status.value = "正在刷新微信窗口列表..."
        self.page.update()

        def run() -> None:
            locator = WeChatWindowLocator(self.config, self.logger)
            windows = locator.find_chat_windows()
            self.event_queue.put(("wechat_windows", windows))

        threading.Thread(target=run, daemon=True).start()

    def select_region(self, _event=None) -> None:
        self._snack("请拖拽框选区域，Esc 取消")

        def run() -> None:
            bbox = select_screen_region()
            if bbox:
                self.config.region.bbox = bbox
                self.config.region.enabled = True
                self.event_queue.put(("region_selected", bbox))
            else:
                self.event_queue.put(("notify_status", "区域框选已取消"))

        threading.Thread(target=run, daemon=True).start()

    def test_selected_region(self, _event=None) -> None:
        self.save_from_ui(show_message=False)
        if not self.config.region.bbox:
            self._snack("请先框选区域", DANGER)
            return
        monitor = WindowMonitor(
            self.config,
            self.logger,
            on_message=lambda message, reason: self.event_queue.put(("message", (message, reason))),
            on_status=lambda status: self.event_queue.put(("status", status)),
        )
        threading.Thread(target=monitor.process_region, daemon=True).start()
        self._snack("已执行一次区域 OCR")

    def test_locate_wechat_window(self, _event=None) -> None:
        self.save_from_ui(show_message=False)
        self.auto_probe_status.value = "正在定位微信窗口..."
        self.page.update()

        def run() -> None:
            locator = WeChatWindowLocator(self.config, self.logger)
            window = locator.best_chat_window()
            if window is None:
                result = AutoWindowOcrResult(False, "未找到可用微信聊天窗口")
            else:
                bbox = locator.chat_area_rect(window)
                result = AutoWindowOcrResult(True, "已定位微信窗口", window=window, bbox=bbox)
            self.event_queue.put(("auto_window_probe", result))

        threading.Thread(target=run, daemon=True).start()

    def preview_auto_window(self, _event=None) -> None:
        self.save_from_ui(show_message=False)
        self.auto_probe_status.value = "正在截取微信窗口预览..."
        self.page.update()

        def run() -> None:
            locator = WeChatWindowLocator(self.config, self.logger)
            window = locator.best_chat_window()
            if window is None:
                result = AutoWindowOcrResult(False, "未找到可用微信聊天窗口")
            else:
                monitor = WindowMonitor(
                    self.config,
                    self.logger,
                    on_message=lambda message, reason: self.event_queue.put(("message", (message, reason))),
                    on_status=lambda status: self.event_queue.put(("status", status)),
                )
                image_path = monitor.capture_auto_window_region(window.rect)
                result = AutoWindowOcrResult(
                    ok=bool(image_path),
                    message="已截取微信窗口预览" if image_path else "微信窗口截图失败",
                    window=window,
                    bbox=window.rect,
                    image_path=image_path,
                )
            self.event_queue.put(("auto_window_probe", result))

        threading.Thread(target=run, daemon=True).start()

    def select_auto_window_message_area(self, _event=None) -> None:
        self.save_from_ui(show_message=False)
        self._snack("正在定位微信窗口，随后请在窗口内框选消息区")

        def run() -> None:
            locator = WeChatWindowLocator(self.config, self.logger)
            window = locator.best_chat_window()
            if window is None:
                self.event_queue.put(("auto_window_probe", AutoWindowOcrResult(False, "未找到可用微信聊天窗口")))
                return
            bbox = select_screen_region(
                window.rect,
                "在微信窗口内拖拽框选消息区域，Esc 取消",
            )
            if not bbox:
                self.event_queue.put(("notify_status", "自动窗口消息区框选已取消"))
                return
            left, top, right, bottom = bbox
            win_left, win_top, win_right, win_bottom = window.rect
            margins = {
                "crop_left": max(0, left - win_left),
                "crop_top": max(0, top - win_top),
                "crop_right": max(0, win_right - right),
                "crop_bottom": max(0, win_bottom - bottom),
            }
            self.event_queue.put(("auto_window_margins", (window, bbox, margins)))

        threading.Thread(target=run, daemon=True).start()

    def test_auto_window_ocr(self, _event=None) -> None:
        self.save_from_ui(show_message=False)
        self.auto_probe_status.value = "正在执行自动窗口 OCR..."
        self.page.update()

        def run() -> None:
            monitor = WindowMonitor(
                self.config,
                self.logger,
                on_message=lambda message, reason: self.event_queue.put(("message", (message, reason))),
                on_status=lambda status: self.event_queue.put(("status", status)),
            )
            result = monitor.process_auto_window(trigger_alert=False, keep_screenshot=True)
            self.event_queue.put(("auto_window_probe", result))

        threading.Thread(target=run, daemon=True).start()

    def test_rule_alert(self, _event=None) -> None:
        self.save_from_ui(show_message=False)
        message = parse_message(self.test_text.value.strip())
        result = match_rules(message, self.config.rules)
        if not result.matched:
            self._snack("未命中规则", DANGER)
            return
        self._create_alert(message, result.reason)

    def test_alarm(self, _event=None) -> None:
        self.alarm.start(self._selected_alarm_sound())
        self._snack("报警音已开始播放")

    def test_notification(self, _event=None) -> None:
        self.save_from_ui(show_message=False)
        message = parse_message(self.test_text.value.strip())
        alert = Alert(message=message, trigger_reason="manual", escalation_minutes=self.config.app.escalation_minutes)
        self._send_notification_async(alert, manual=True, allow_auto_retry=False)

    def record_mouse_position_later(self, _event=None) -> None:
        self._snack("3 秒内把鼠标移到联系人聊天窗口输入框")

        def capture() -> None:
            time.sleep(3)
            try:
                import pyautogui
            except Exception as exc:  # noqa: BLE001
                self.event_queue.put(("notify_status", f"缺少 pyautogui：{exc}"))
                return
            pos = pyautogui.position()
            self.config.notification.automation_input_pos = [int(pos.x), int(pos.y)]
            self.event_queue.put(("automation_pos", [int(pos.x), int(pos.y)]))

        threading.Thread(target=capture, daemon=True).start()

    def refresh_logs(self, _event=None) -> None:
        path = Path("logs/app.log")
        self.log_text.value = path.read_text(encoding="utf-8")[-16000:] if path.exists() else ""
        self.page.update()

    def cleanup_files(self, _event=None) -> None:
        self.save_from_ui(show_message=False)
        result = cleanup_runtime_files(keep_screenshots=self.config.app.cleanup_screenshots_keep)
        self.logger.info(result.message)
        self._snack(result.message)
        self.refresh_logs()

    def _create_alert(self, message: Message, reason: str) -> None:
        alert = Alert(message=message, trigger_reason=reason, escalation_minutes=self.config.app.escalation_minutes)
        self.current_alerts[alert.id] = alert
        self.logger.info("开始强提醒 alert=%s reason=%s", alert.id, reason)
        self.alarm.start(self.config.app.alarm_sound)
        self._show_alert(alert)

    def _show_alert(self, alert: Alert) -> None:
        elapsed = ft.Text("已提醒：00:00", size=13, color=MUTED)
        remaining = ft.Text("", size=13, color=MUTED)

        def tick() -> None:
            while self._alert_timer_active(alert) and self.running:
                now = datetime.now()
                elapsed_seconds = max(0, int((now - alert.created_at).total_seconds()))
                elapsed.value = f"已提醒：{elapsed_seconds // 60:02d}:{elapsed_seconds % 60:02d}"
                if alert.status == "failed" and alert.next_notify_at:
                    retry_seconds = max(0, int((alert.next_notify_at - now).total_seconds()))
                    remaining.value = (
                        f"通知失败，将在：{retry_seconds // 60:02d}:{retry_seconds % 60:02d} 后重试"
                    )
                else:
                    remaining_seconds = max(0, int((alert.deadline_at - now).total_seconds()))
                    remaining.value = f"将在：{remaining_seconds // 60:02d}:{remaining_seconds % 60:02d} 后通知联系人"
                self._check_escalation(alert)
                if self.running and self._alert_timer_active(alert):
                    self.event_queue.put(("update", None))
                time.sleep(1)

        def close_dialog() -> None:
            self._close_dialogs()

        def finish_resolve() -> None:
            for active_alert in self.current_alerts.values():
                if active_alert.status != "resolved":
                    active_alert.resolve()
                active_alert.notify_inflight = False
            self.current_alerts.clear()
            self.alarm.stop()
            close_dialog()
            self.stop_monitoring(show_message=False)
            self.notify_status.value = "通知状态：已确认处理，提醒和监控已关闭"
            self.page.update()

        def ask_resolve_confirm(_event=None) -> None:
            close_dialog()

            def cancel(_=None) -> None:
                close_dialog()
                self.page.show_dialog(dialog)

            def confirm(_=None) -> None:
                close_dialog()
                finish_resolve()

            confirm_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("确认已处理", weight=ft.FontWeight.BOLD),
                content=ft.Text("确认这条提醒已经处理完成？确认后会自动停止当前监控。"),
                actions=[
                    ft.OutlinedButton("取消", on_click=cancel),
                    ft.FilledButton(
                        "已确认",
                        icon=ICONS.CHECK,
                        on_click=confirm,
                        style=ft.ButtonStyle(bgcolor=SUCCESS, color="#FFFFFF"),
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.show_dialog(confirm_dialog)

        def delay(_event=None) -> None:
            alert.delay(5)
            self._snack("已延迟 5 分钟")

        def notify(_event=None) -> None:
            self._send_notification_async(alert, manual=True)

        content = ft.Container(
            width=520,
            content=ft.Column(
                tight=True,
                spacing=12,
                controls=[
                    ft.Row([ft.Text("群：", color=MUTED), ft.Text(alert.message.chat_name or "未识别", color=TEXT)]),
                    ft.Container(
                        height=150,
                        bgcolor="#F8FAFC",
                        border_radius=12,
                        padding=12,
                        content=ft.Column(
                            scroll=ft.ScrollMode.AUTO,
                            controls=[ft.Text(alert.message.content or alert.message.raw_text, selectable=True)],
                        ),
                    ),
                    elapsed,
                    remaining,
                ],
            ),
        )
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("微信强提醒", weight=ft.FontWeight.BOLD),
            content=content,
            actions=[
                ft.FilledButton(
                    "我已处理",
                    icon=ICONS.CHECK,
                    on_click=ask_resolve_confirm,
                    style=ft.ButtonStyle(bgcolor=SUCCESS, color="#FFFFFF"),
                ),
                ft.OutlinedButton("延迟5分钟", icon=ICONS.SNOOZE, on_click=delay),
                ft.FilledButton(
                    "立即通知",
                    icon=ICONS.SEND,
                    on_click=notify,
                    style=ft.ButtonStyle(bgcolor=PRIMARY, color="#FFFFFF"),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)
        self.page.run_thread(tick)

    def resolve_alert(self, alert: Alert) -> None:
        alert.resolve()
        alert.notify_inflight = False
        self.alarm.stop()

    def delay_alert(self, alert: Alert) -> None:
        alert.delay(5)
        self._snack("已延迟 5 分钟")

    def notify_alert(self, alert: Alert) -> None:
        self._send_notification_async(alert, manual=True)

    def _notification_max_attempts(self) -> int:
        return 1 + max(0, int(self.config.notification.retry_attempts))

    def _notification_retry_delay_seconds(self) -> int:
        return max(1, int(self.config.notification.retry_delay_seconds))

    def _alert_timer_active(self, alert: Alert) -> bool:
        return alert.status in ("pending", "delayed") or (
            alert.status == "failed"
            and (alert.notify_inflight or alert.can_retry_notification(self._notification_max_attempts()))
        )

    def _check_escalation(self, alert: Alert) -> None:
        if alert.status == "resolved" or alert.notified_contact or alert.notify_inflight:
            return

        current = datetime.now()
        if alert.status in ("pending", "delayed") and current >= alert.deadline_at:
            self._send_notification_async(alert)
        elif alert.notification_retry_due(self._notification_max_attempts(), current):
            self._send_notification_async(alert)

    def _send_notification_async(self, alert: Alert, manual: bool = False, allow_auto_retry: bool = True) -> None:
        if alert.status == "resolved":
            self.logger.info("提醒已处理，跳过通知请求 alert=%s", alert.id)
            return
        if alert.notified_contact:
            self.logger.info("通知已完成，跳过重复请求 alert=%s", alert.id)
            self.notify_status.value = "通知状态：已通知，已忽略重复请求"
            self.page.update()
            return
        if alert.notify_inflight:
            self.logger.info("通知已在进行中，跳过重复请求 alert=%s", alert.id)
            self.notify_status.value = "通知状态：正在执行中，已忽略重复请求"
            self.page.update()
            return
        max_attempts = self._notification_max_attempts()
        current = datetime.now()
        if alert.status == "failed" and not manual:
            if not alert.can_retry_notification(max_attempts):
                self.notify_status.value = (
                    f"通知状态：失败，已达到重试上限（{alert.notification_attempts}/{max_attempts}）"
                )
                self.page.update()
                return
            if alert.next_notify_at and current < alert.next_notify_at:
                remaining_seconds = int((alert.next_notify_at - current).total_seconds())
                self.notify_status.value = f"通知状态：等待重试，约 {remaining_seconds} 秒后再试"
                self.page.update()
                return

        attempt = alert.begin_notification()

        def run() -> None:
            self.event_queue.put(("notify_status", f"通知状态：正在执行第 {attempt} 次..."))
            try:
                result = send_notification(alert, self.config.notification, self.logger)
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("通知线程异常 alert=%s", alert.id)
                result = NotifyResult(False, f"通知线程异常：{exc}")
            self.event_queue.put(("notify_result", (alert, result.ok, result.message, allow_auto_retry)))

        threading.Thread(target=run, daemon=True).start()

    def _pump_events(self) -> None:
        while self.running:
            try:
                event, payload = self.event_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if event == "message":
                    message, reason = payload
                    self._create_alert(message, reason)
                elif event == "status":
                    self.status_text.value = str(payload)
                    self.page.update()
                elif event == "notify_status":
                    self.notify_status.value = str(payload)
                    self.page.update()
                elif event == "notify_result":
                    alert, ok, message, allow_auto_retry = payload
                    alert.notify_inflight = False
                    if alert.status == "resolved":
                        self.logger.info("提醒已处理，忽略迟到的通知结果 alert=%s", alert.id)
                    elif ok:
                        alert.escalate()
                        self.logger.info("通知联系人成功 alert=%s message=%s", alert.id, message)
                        self.notify_status.value = f"通知状态：成功 - {message}"
                        self._snack(message)
                    else:
                        max_attempts = self._notification_max_attempts()
                        if allow_auto_retry and alert.notification_attempts < max_attempts:
                            delay_seconds = self._notification_retry_delay_seconds()
                            alert.schedule_notification_retry(message, delay_seconds)
                            self.logger.error(
                                "通知联系人失败 alert=%s attempt=%s/%s retry_in=%ss message=%s",
                                alert.id,
                                alert.notification_attempts,
                                max_attempts,
                                delay_seconds,
                                message,
                            )
                            self.notify_status.value = (
                                f"通知状态：失败 - {message}；{delay_seconds} 秒后自动重试 "
                                f"（{alert.notification_attempts}/{max_attempts}）"
                            )
                        else:
                            alert.fail_notification_permanently(message)
                            self.logger.error(
                                "通知联系人失败且达到重试上限 alert=%s attempts=%s message=%s",
                                alert.id,
                                alert.notification_attempts,
                                message,
                            )
                            self.notify_status.value = (
                                f"通知状态：失败 - {message}；已达到重试上限 "
                                f"（{alert.notification_attempts}/{max_attempts}）"
                            )
                        self._snack(message, DANGER)
                    self.refresh_logs()
                elif event == "auto_window_probe":
                    result = payload
                    if isinstance(result, AutoWindowOcrResult):
                        parts = [result.message]
                        if result.window:
                            parts.append(
                                "窗口："
                                f"{result.window.title or '-'} / {result.window.class_name or '-'} / "
                                f"{result.window.width}x{result.window.height}"
                            )
                        if result.bbox:
                            left, top, right, bottom = result.bbox
                            parts.append(f"聊天区：{left}, {top}, {right}, {bottom} ({right - left}x{bottom - top})")
                            if result.window and result.window.area:
                                crop_ratio = ((right - left) * (bottom - top)) / result.window.area
                                if crop_ratio >= 0.85:
                                    parts.append("裁剪风险：当前区域覆盖窗口超过 85%，请只框选消息列表")
                        if result.matched:
                            parts.append(f"命中规则：{result.reason}")
                        if result.matched_items:
                            parts.append("命中项：" + "、".join(result.matched_items))
                        if result.cross_source_duplicate:
                            parts.append("跨模式重复：已抑制提醒")
                        elif result.duplicate:
                            parts.append("命中去重：是")
                        elif result.skipped_existing:
                            parts.append("启动基线：已忽略历史文本")
                        if result.image_path:
                            parts.append(f"截图：{result.image_path}")
                            self.auto_preview.src = str(result.image_path.resolve())
                            self.auto_preview.visible = True
                        self.auto_probe_status.value = "\n".join(parts)
                        self.auto_probe_status.color = SUCCESS if result.ok else DANGER
                        self.auto_probe_text.value = self._format_auto_ocr_debug(result)
                        self._snack(result.message, SUCCESS if result.ok else DANGER)
                        self.page.update()
                elif event == "auto_window_margins":
                    window, bbox, margins = payload
                    self.config.auto_window_ocr.crop_left = int(margins["crop_left"])
                    self.config.auto_window_ocr.crop_top = int(margins["crop_top"])
                    self.config.auto_window_ocr.crop_right = int(margins["crop_right"])
                    self.config.auto_window_ocr.crop_bottom = int(margins["crop_bottom"])
                    self.auto_crop_left.value = str(self.config.auto_window_ocr.crop_left)
                    self.auto_crop_top.value = str(self.config.auto_window_ocr.crop_top)
                    self.auto_crop_right.value = str(self.config.auto_window_ocr.crop_right)
                    self.auto_crop_bottom.value = str(self.config.auto_window_ocr.crop_bottom)
                    save_config(self.config)
                    left, top, right, bottom = bbox
                    self.auto_probe_status.value = (
                        "消息区边距已更新\n"
                        f"窗口：{window.title or '-'} / {window.class_name or '-'} / {window.width}x{window.height}\n"
                        f"消息区：{left}, {top}, {right}, {bottom} ({right - left}x{bottom - top})\n"
                        f"边距：左 {margins['crop_left']} / 上 {margins['crop_top']} / "
                        f"右 {margins['crop_right']} / 下 {margins['crop_bottom']}"
                    )
                    self.auto_probe_status.color = SUCCESS
                    self._snack("消息区边距已保存")
                    self.page.update()
                elif event == "wechat_windows":
                    windows = payload
                    options = []
                    for window in windows:
                        title = window.title or f"窗口 {window.hwnd}"
                        value = self._window_select_value(window.hwnd, title)
                        label = f"{title} | {window.process_name} | {window.class_name} | {window.width}x{window.height}"
                        options.append(ft.dropdown.Option(key=value, text=label))
                    self.auto_window_select.options = options
                    current_value = self._window_select_value(
                        self.config.auto_window_ocr.selected_window_hwnd,
                        self.config.auto_window_ocr.selected_window_title,
                    )
                    option_values = {option.key for option in options}
                    if current_value in option_values:
                        self.auto_window_select.value = current_value
                    elif options:
                        self.auto_window_select.value = options[0].key
                    else:
                        self.auto_window_select.value = None
                    self._save_selected_auto_window()
                    if options:
                        self.auto_probe_status.value = f"已找到 {len(options)} 个微信窗口，请选择要监控的窗口"
                        self.auto_probe_status.color = SUCCESS
                        self._snack("窗口列表已刷新")
                    else:
                        self.auto_probe_status.value = "未找到可用微信窗口"
                        self.auto_probe_status.color = DANGER
                        self._snack("未找到可用微信窗口", DANGER)
                    save_config(self.config)
                    self.page.update()
                elif event == "region_selected":
                    self.region_enabled.value = True
                    self.region_bbox.value = self._format_bbox()
                    save_config(self.config)
                    self._snack("区域已保存")
                    self.page.update()
                elif event == "automation_pos":
                    save_config(self.config)
                    self.automation_pos.value = self._format_automation_pos()
                    self._snack("已记录输入框坐标")
                    self.page.update()
                elif event == "tray":
                    self._handle_tray_action(str(payload))
                elif event == "update":
                    self.page.update()
            except RuntimeError as exc:
                if "Event loop is closed" in str(exc):
                    self.logger.warning("Flet event loop 已关闭，停止后台任务")
                    self.running = False
                    if self.monitor:
                        self.monitor.stop()
                    self.alarm.stop()
                    break
                self.logger.exception("Flet event pump failed")
            except Exception:
                self.logger.exception("Flet event pump failed")

    def _handle_tray_action(self, action: str) -> None:
        if action == "show":
            self._show_main_window()
        elif action == "start":
            self.start_monitoring()
        elif action == "stop":
            self.stop_monitoring()
        elif action == "alarm":
            self.test_alarm()
        elif action == "exit":
            self.exit_requested = True
            self.close()
            for method_name in ("window_close", "window_destroy"):
                method = getattr(self.page, method_name, None)
                if callable(method):
                    try:
                        method()
                        break
                    except Exception:
                        continue

    def _show_main_window(self) -> None:
        for attr, value in (
            ("window_visible", True),
            ("window_minimized", False),
            ("window_focused", True),
        ):
            try:
                setattr(self.page, attr, value)
            except Exception:
                pass
        try:
            self.page.window_to_front()
        except Exception:
            pass
        self.page.update()

    def on_window_event(self, event) -> None:
        if getattr(event, "data", "") == "close" and not self.exit_requested:
            self.page.window_visible = False
            self.page.update()
            return
        if getattr(event, "data", "") == "close":
            self.close()

    def _snack(self, message: str, color: str = PRIMARY) -> None:
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(message, color="#FFFFFF"),
            bgcolor=color,
            show_close_icon=True,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _close_dialogs(self) -> None:
        for _ in range(4):
            try:
                self.page.pop_dialog()
            except Exception:
                break
        try:
            if getattr(self.page, "dialog", None):
                self.page.dialog.open = False
        except Exception:
            pass
        self.page.update()

    def _format_bbox(self) -> str:
        bbox = self.config.region.bbox
        if len(bbox) == 4:
            left, top, right, bottom = bbox
            return f"{left}, {top}, {right}, {bottom}  ({right - left} x {bottom - top})"
        return "未选择"

    def _format_automation_pos(self) -> str:
        pos = self.config.notification.automation_input_pos
        return f"{pos[0]}, {pos[1]}" if len(pos) == 2 else "未记录"

    @staticmethod
    def _alarm_preset_value(alarm_sound: str) -> str:
        preset_keys = {key for key, _label in alarm_preset_options()}
        return alarm_sound if alarm_sound in preset_keys else "custom"

    def _selected_alarm_sound(self) -> str:
        value = str(self.alarm_preset.value or "")
        if value.startswith("preset:"):
            return value
        return self.alarm_sound.value.strip()

    @staticmethod
    def _format_auto_ocr_debug(result: AutoWindowOcrResult) -> str:
        sections: list[str] = []
        if result.image_path:
            sections.append(f"截图路径:\n{result.image_path}")
        if result.matched or result.matched_items or result.duplicate or result.skipped_existing:
            sections.append(
                "判断结果:\n"
                f"matched={result.matched}\n"
                f"reason={result.reason or '-'}\n"
                f"chat_matched={result.chat_matched}\n"
                f"duplicate={result.duplicate}\n"
                f"cross_source_duplicate={result.cross_source_duplicate}\n"
                f"skipped_existing={result.skipped_existing}\n"
                f"matched_items={', '.join(result.matched_items) if result.matched_items else '-'}"
            )
        if result.new_lines:
            sections.append("新增行:\n" + "\n".join(result.new_lines))
        if result.processed_text:
            sections.append("参与匹配文本:\n" + result.processed_text)
        if result.normalized_text:
            sections.append("清洗后 OCR 文本:\n" + result.normalized_text)
        if result.raw_text:
            sections.append("原始 OCR 文本:\n" + result.raw_text)
        return "\n\n---\n\n".join(sections)

    @staticmethod
    def _window_select_value(hwnd: int, title: str) -> str:
        title = (title or "").strip()
        if not hwnd and not title:
            return ""
        return f"{int(hwnd or 0)}\t{title}"

    def _save_selected_auto_window(self) -> None:
        value = str(self.auto_window_select.value or "")
        if not value:
            return
        if "\t" in value:
            hwnd_text, title = value.split("\t", 1)
            self.config.auto_window_ocr.selected_window_hwnd = self._int_value(hwnd_text, 0)
            self.config.auto_window_ocr.selected_window_title = title.strip()
        else:
            self.config.auto_window_ocr.selected_window_hwnd = 0
            self.config.auto_window_ocr.selected_window_title = value.strip()

    @staticmethod
    def _lines(value: str) -> list[str]:
        return [line.strip() for line in value.splitlines() if line.strip()]

    @staticmethod
    def _int_value(value: str, default: int) -> int:
        try:
            return int(str(value).strip())
        except ValueError:
            return default

    @staticmethod
    def _threshold_value(value: str, default: float) -> float:
        try:
            return max(0.0, min(1.0, float(str(value).strip())))
        except ValueError:
            return default

    def close(self) -> None:
        self.running = False
        if self.monitor:
            self.monitor.stop()
        self.alarm.stop()
        self.keepalive.stop()
        self.tray.stop()
        self.logger.info("软件退出")


def main(page: ft.Page) -> None:
    logger = setup_logging()
    try:
        config = load_config()
        app = FletAssistantApp(page, config, logger)
        if config.app.enable_tray:
            page.on_window_event = app.on_window_event
        page.on_close = lambda _event: app.close()
    except Exception:
        logger.exception("Flet 主界面初始化失败")
        raise


def run() -> None:
    ft.app(target=main)
