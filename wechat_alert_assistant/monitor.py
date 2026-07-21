from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .assets import resolve_ocr_model_dirs
from .config import Config
from .dedup import CrossSourceAlertDeduplicator, OcrLineDeduplicator
from .housekeeping import cleanup_screenshots
from .models import Message
from .rules import is_monitored_chat, match_region_rules, match_rules, matched_rule_items, parse_message
from .text_utils import normalize_text
from .wechat_window import WeChatWindow, WeChatWindowLocator


try:
    import os

    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    import win32gui
    import win32process
except Exception:  # noqa: BLE001
    win32gui = None
    win32process = None

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None


@dataclass(slots=True)
class WindowInfo:
    hwnd: int
    title: str
    rect: tuple[int, int, int, int]
    process_name: str

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])


@dataclass(slots=True)
class AutoWindowOcrResult:
    ok: bool
    message: str
    window: WeChatWindow | None = None
    bbox: tuple[int, int, int, int] | None = None
    image_path: Path | None = None
    raw_text: str = ""
    normalized_text: str = ""
    processed_text: str = ""
    new_lines: list[str] = field(default_factory=list)
    matched: bool = False
    reason: str = ""
    matched_items: list[str] = field(default_factory=list)
    chat_matched: bool = False
    duplicate: bool = False
    skipped_existing: bool = False
    cross_source_duplicate: bool = False


class OcrEngine:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._ocr = None
        self._available = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                from paddleocr import PaddleOCR

                model_dirs = resolve_ocr_model_dirs()
                if model_dirs:
                    self.logger.info("PaddleOCR using bundled models: %s", model_dirs)
                self._ocr = PaddleOCR(
                    use_angle_cls=True,
                    lang="ch",
                    enable_mkldnn=False,
                    show_log=False,
                    **model_dirs,
                )
                self._available = True
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("PaddleOCR 不可用：%s", exc)
                self._available = False
        return bool(self._available)

    def recognize(self, image_path: Path) -> str:
        if not self.available or self._ocr is None:
            return ""
        result = self._ocr.ocr(str(image_path), cls=True)
        lines: list[str] = []
        for block in result or []:
            for item in block or []:
                if len(item) >= 2 and item[1]:
                    lines.append(str(item[1][0]))
        return "\n".join(lines)


class WindowMonitor:
    def __init__(
        self,
        config: Config,
        logger: logging.Logger,
        on_message: Callable[[Message, str], None],
        on_status: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.logger = logger
        self.on_message = on_message
        self.on_status = on_status or (lambda _: None)
        self.ocr = OcrEngine(logger)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_hwnds: set[int] = set()
        self._popup_seen_texts: dict[str, datetime] = {}
        self._line_deduplicator = OcrLineDeduplicator()
        self._alert_deduplicator = CrossSourceAlertDeduplicator()
        self._auto_window_initialized = False
        self._region_initialized = False
        self._next_auto_window_scan = datetime.min
        self._next_region_scan = datetime.min
        self.window_locator = WeChatWindowLocator(config, logger)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self) -> None:
        self.logger.info("监控启动")
        while not self._stop.is_set():
            try:
                if self.config.auto_window_ocr.enabled:
                    self.process_auto_window_if_due()
                if self.config.region.enabled and self.config.region.bbox:
                    self.process_region_if_due()
                if not self.config.wechat.enable_popup_ocr:
                    active_modes = self._active_mode_text()
                    if active_modes:
                        self.on_status(f"正在监控：{active_modes}")
                    else:
                        self.on_status("微信弹窗监控已关闭，且未启用区域监控")
                elif not self.is_supported():
                    active_modes = self._active_mode_text()
                    if active_modes:
                        self.on_status(f"正在监控：{active_modes}")
                    else:
                        self.on_status("缺少 pywin32/psutil，无法真实枚举微信窗口")
                else:
                    windows = self.find_candidate_windows()
                    extra_status = self._active_mode_text(exclude_popup=True)
                    extra_status = f"，{extra_status}已启用" if extra_status else ""
                    self.on_status(f"正在监控，候选弹窗 {len(windows)} 个{extra_status}")
                    for window in windows:
                        self.process_window(window)
                time.sleep(max(0.1, self.config.app.poll_interval_ms / 1000))
            except Exception:
                self.logger.exception("监控循环异常")
                time.sleep(1)
        self.logger.info("监控停止")

    def _active_mode_text(self, exclude_popup: bool = False) -> str:
        modes: list[str] = []
        if self.config.auto_window_ocr.enabled:
            modes.append("自动窗口 OCR")
        if self.config.region.enabled and self.config.region.bbox:
            modes.append("区域 OCR")
        if not exclude_popup and self.config.wechat.enable_popup_ocr:
            modes.append("弹窗 OCR")
        return "、".join(modes)

    @staticmethod
    def is_supported() -> bool:
        return bool(win32gui and win32process and psutil)

    def find_candidate_windows(self) -> list[WindowInfo]:
        if not self.is_supported():
            return []
        result: list[WindowInfo] = []

        def callback(hwnd, _extra):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            rect = win32gui.GetWindowRect(hwnd)
            width = max(0, rect[2] - rect[0])
            height = max(0, rect[3] - rect[1])
            if not (self.config.wechat.min_width <= width <= self.config.wechat.max_width):
                return
            if not (self.config.wechat.min_height <= height <= self.config.wechat.max_height):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                process_name = psutil.Process(pid).name()
            except Exception:
                return
            if process_name.lower() != self.config.wechat.process_name.lower():
                return
            if hwnd in self._seen_hwnds:
                return
            result.append(WindowInfo(hwnd=hwnd, title=title, rect=rect, process_name=process_name))

        win32gui.EnumWindows(callback, None)
        return result

    def process_window(self, window: WindowInfo) -> None:
        self._seen_hwnds.add(window.hwnd)
        self.logger.info("检测到候选微信弹窗 hwnd=%s rect=%s title=%s", window.hwnd, window.rect, window.title)
        image_path = self.capture_window(window)
        if not image_path:
            return
        raw_text = self.ocr.recognize(image_path)
        if not self.config.app.debug_save_screenshots:
            try:
                image_path.unlink(missing_ok=True)
            except Exception:
                self.logger.debug("临时截图清理失败：%s", image_path)
        if self.config.app.log_ocr_text:
            self.logger.info("OCR结果 hwnd=%s text=%s", window.hwnd, raw_text)
        if not raw_text:
            return
        key = self._text_key("popup", raw_text)
        now = datetime.now()
        self._popup_seen_texts = {
            k: v for k, v in self._popup_seen_texts.items() if now - v < timedelta(seconds=30)
        }
        if key in self._popup_seen_texts:
            self.logger.info("忽略重复弹窗 text_hash=%s", key)
            return
        self._popup_seen_texts[key] = now

        message = parse_message(raw_text)
        message.source = "wechat_popup_ocr"
        match = match_rules(message, self.config.rules)
        if match.matched:
            self.logger.info("命中规则 reason=%s chat=%s content=%s", match.reason, message.chat_name, message.content)
            self._emit_match(message, match.reason, self.config.auto_window_ocr.dedup_similarity)

    def process_region_if_due(self) -> None:
        now = datetime.now()
        if now < self._next_region_scan:
            return
        self._next_region_scan = now + timedelta(seconds=max(5, self.config.region.poll_interval_seconds))
        self.process_region()

    def process_auto_window_if_due(self) -> None:
        now = datetime.now()
        if now < self._next_auto_window_scan:
            return
        self._next_auto_window_scan = now + timedelta(seconds=max(1, self.config.auto_window_ocr.poll_interval_seconds))
        self.process_auto_window()

    def process_region(self) -> None:
        image_path = self.capture_region()
        if not image_path:
            return
        raw_text = self.ocr.recognize(image_path)
        if not self.config.app.debug_save_screenshots:
            try:
                image_path.unlink(missing_ok=True)
            except Exception:
                self.logger.debug("区域临时截图清理失败：%s", image_path)
        if self.config.app.log_ocr_text:
            self.logger.info("区域OCR结果 text=%s", raw_text)
        if not raw_text:
            return

        current = datetime.now()
        normalized_text = normalize_text(raw_text)
        recent_lines = self._recent_lines(normalized_text, self.config.region.max_recent_lines)
        new_lines = self._line_deduplicator.new_lines(
            "region",
            recent_lines,
            current,
            self.config.region.dedup_seconds,
            self.config.region.dedup_similarity,
        )
        if not self._region_initialized and self.config.region.skip_existing_on_start:
            self._line_deduplicator.mark_lines(
                "region",
                recent_lines,
                current,
                self.config.region.dedup_seconds,
                self.config.region.dedup_similarity,
            )
            self._region_initialized = True
            self.logger.info("区域 OCR 初始快照完成，已忽略现有文本行 %s 条", len(recent_lines))
            return
        self._region_initialized = True

        if not new_lines:
            self.logger.info("忽略重复区域OCR，最近文本没有新增行")
            return
        self._line_deduplicator.mark_lines(
            "region",
            recent_lines,
            current,
            self.config.region.dedup_seconds,
            self.config.region.dedup_similarity,
        )

        message = parse_message("\n".join(new_lines))
        message.source = "region_ocr"
        match = match_region_rules(message, self.config.rules, self.config.region.require_chat_match)
        if match.matched:
            self.logger.info("区域监控命中 reason=%s content=%s", match.reason, message.raw_text)
            self._emit_match(message, match.reason, self.config.region.dedup_similarity)

    def process_auto_window(self, trigger_alert: bool = True, keep_screenshot: bool = False) -> AutoWindowOcrResult:
        if not self.window_locator.is_supported():
            return AutoWindowOcrResult(False, "缺少 pywin32/psutil，无法定位微信窗口")

        window = self.window_locator.best_chat_window()
        if window is None:
            return AutoWindowOcrResult(False, "未找到可用微信聊天窗口")

        bbox = self.window_locator.chat_area_rect(window)
        if bbox is None:
            return AutoWindowOcrResult(False, "自动推算聊天区失败，请调整裁剪边距", window=window)

        image_path = self.capture_auto_window_region(bbox)
        if not image_path:
            return AutoWindowOcrResult(False, "聊天区截图失败", window=window, bbox=bbox)

        raw_text = self.ocr.recognize(image_path)
        if not keep_screenshot and not self.config.app.debug_save_screenshots:
            try:
                image_path.unlink(missing_ok=True)
            except Exception:
                self.logger.debug("自动窗口 OCR 临时截图清理失败：%s", image_path)

        if self.config.app.log_ocr_text:
            self.logger.info(
                "自动窗口OCR结果 hwnd=%s title=%s bbox=%s text=%s",
                window.hwnd,
                window.title,
                bbox,
                raw_text,
            )
        if not raw_text:
            return AutoWindowOcrResult(False, "OCR 没有识别到文字", window=window, bbox=bbox, image_path=image_path)

        current = datetime.now()
        normalized_text = normalize_text(raw_text)
        recent_lines = self._recent_lines(normalized_text, self.config.auto_window_ocr.max_recent_lines)
        recent_text = "\n".join(recent_lines)
        new_lines = self._line_deduplicator.new_lines(
            "auto_window",
            recent_lines,
            current,
            self.config.auto_window_ocr.dedup_seconds,
            self.config.auto_window_ocr.dedup_similarity,
        )
        if not self._auto_window_initialized and self.config.auto_window_ocr.skip_existing_on_start and trigger_alert:
            self._line_deduplicator.mark_lines(
                "auto_window",
                recent_lines,
                current,
                self.config.auto_window_ocr.dedup_seconds,
                self.config.auto_window_ocr.dedup_similarity,
            )
            self._auto_window_initialized = True
            self.logger.info("自动窗口 OCR 初始快照完成，已忽略现有文本行 %s 条", len(recent_lines))
            return AutoWindowOcrResult(
                True,
                "初始快照完成，已忽略当前历史文本",
                window=window,
                bbox=bbox,
                image_path=image_path,
                raw_text=raw_text,
                normalized_text=normalized_text,
                processed_text=recent_text,
                new_lines=[],
                skipped_existing=True,
            )
        self._auto_window_initialized = True

        if not new_lines:
            self.logger.info("忽略重复自动窗口OCR，最近文本没有新增行")
            return AutoWindowOcrResult(
                True,
                "OCR 成功，但最近文本没有新增行",
                window=window,
                bbox=bbox,
                image_path=image_path,
                raw_text=raw_text,
                normalized_text=normalized_text,
                processed_text=recent_text,
                new_lines=[],
                duplicate=True,
            )
        self._line_deduplicator.mark_lines(
            "auto_window",
            recent_lines,
            current,
            self.config.auto_window_ocr.dedup_seconds,
            self.config.auto_window_ocr.dedup_similarity,
        )

        process_text = "\n".join(new_lines)
        message = parse_message(process_text)
        message.source = "auto_window_ocr"
        match = match_region_rules(message, self.config.rules, self.config.auto_window_ocr.require_chat_match)
        chat_matched, matched_items = self._match_debug(message)
        if match.matched:
            self.logger.info("自动窗口监控命中 reason=%s content=%s", match.reason, message.raw_text)
            cross_source_duplicate = False
            if trigger_alert:
                cross_source_duplicate = not self._emit_match(
                    message,
                    match.reason,
                    self.config.auto_window_ocr.dedup_similarity,
                )
            return AutoWindowOcrResult(
                True,
                "OCR 成功，但跨来源重复提醒已忽略" if cross_source_duplicate else "OCR 成功并命中规则",
                window=window,
                bbox=bbox,
                image_path=image_path,
                raw_text=raw_text,
                normalized_text=normalized_text,
                processed_text=process_text,
                new_lines=new_lines,
                matched=True,
                reason=match.reason,
                matched_items=matched_items,
                chat_matched=chat_matched,
                duplicate=cross_source_duplicate,
                cross_source_duplicate=cross_source_duplicate,
            )
        return AutoWindowOcrResult(
            True,
            "OCR 成功，未命中规则",
            window=window,
            bbox=bbox,
            image_path=image_path,
            raw_text=raw_text,
            normalized_text=normalized_text,
            processed_text=process_text,
            new_lines=new_lines,
            matched_items=matched_items,
            chat_matched=chat_matched,
        )

    def capture_window(self, window: WindowInfo) -> Path | None:
        try:
            from PIL import ImageGrab, ImageOps, ImageEnhance

            image = ImageGrab.grab(bbox=window.rect)
            image = image.resize((image.width * 2, image.height * 2))
            image = ImageOps.grayscale(image)
            image = ImageEnhance.Contrast(image).enhance(1.6)
            Path("screenshots_debug").mkdir(exist_ok=True)
            path = Path("screenshots_debug") / f"popup_{window.hwnd}_{int(time.time())}.png"
            image.save(path)
            cleanup_screenshots(keep=self.config.app.cleanup_screenshots_keep)
            return path
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("截图失败：%s", exc)
            return None

    def capture_region(self) -> Path | None:
        try:
            from PIL import ImageEnhance, ImageGrab, ImageOps

            left, top, right, bottom = self.config.region.bbox
            if right <= left or bottom <= top:
                self.logger.warning("区域坐标无效：%s", self.config.region.bbox)
                return None
            image = ImageGrab.grab(bbox=(left, top, right, bottom))
            image = image.resize((image.width * 2, image.height * 2))
            image = ImageOps.grayscale(image)
            image = ImageEnhance.Contrast(image).enhance(1.6)
            Path("screenshots_debug").mkdir(exist_ok=True)
            path = Path("screenshots_debug") / f"region_{int(time.time())}.png"
            image.save(path)
            cleanup_screenshots(keep=self.config.app.cleanup_screenshots_keep)
            return path
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("区域截图失败：%s", exc)
            return None

    def capture_auto_window_region(self, bbox: tuple[int, int, int, int]) -> Path | None:
        try:
            from PIL import ImageEnhance, ImageGrab, ImageOps

            image = ImageGrab.grab(bbox=bbox)
            image = image.resize((image.width * 2, image.height * 2))
            image = ImageOps.grayscale(image)
            image = ImageEnhance.Contrast(image).enhance(1.6)
            Path("screenshots_debug").mkdir(exist_ok=True)
            path = Path("screenshots_debug") / f"auto_window_{int(time.time())}.png"
            image.save(path)
            cleanup_screenshots(keep=self.config.app.cleanup_screenshots_keep)
            return path
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("自动窗口 OCR 截图失败：%s", exc)
            return None

    @staticmethod
    def _text_key(source: str, raw_text: str) -> str:
        return hashlib.sha1(f"{source}\n{raw_text}".encode("utf-8")).hexdigest()

    @staticmethod
    def _recent_text(raw_text: str, max_lines: int) -> str:
        lines = WindowMonitor._recent_lines(raw_text, max_lines)
        if not lines:
            return raw_text.strip()
        return "\n".join(lines)

    @staticmethod
    def _recent_lines(raw_text: str, max_lines: int) -> list[str]:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        return lines[-max(1, max_lines) :]

    def _match_debug(self, message: Message) -> tuple[bool, list[str]]:
        matched_items = matched_rule_items(message, self.config.rules)
        chat_matched = is_monitored_chat(message, self.config.rules)
        if chat_matched:
            matched_items.insert(0, "监控群名")
        return chat_matched, matched_items

    def _emit_match(self, message: Message, reason: str, similarity_threshold: float) -> bool:
        evidence = matched_rule_items(message, self.config.rules)
        duplicate = self._alert_deduplicator.check_and_mark(
            source=message.source,
            reason=reason,
            raw_text=message.raw_text,
            evidence=evidence,
            current=datetime.now(),
            retention_seconds=self.config.app.cross_source_dedup_seconds,
            similarity_threshold=similarity_threshold,
        )
        if duplicate:
            self.logger.info(
                "忽略跨来源重复提醒 source=%s reason=%s evidence=%s",
                message.source,
                reason,
                evidence,
            )
            return False
        self.on_message(message, reason)
        return True
