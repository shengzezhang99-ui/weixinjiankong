from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Config


try:
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
class WeChatWindow:
    hwnd: int
    title: str
    class_name: str
    rect: tuple[int, int, int, int]
    process_name: str
    minimized: bool = False

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    @property
    def area(self) -> int:
        return self.width * self.height


class WeChatWindowLocator:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger

    @staticmethod
    def is_supported() -> bool:
        return bool(win32gui and win32process and psutil)

    def find_chat_windows(self) -> list[WeChatWindow]:
        if not self.is_supported():
            return []

        windows: list[WeChatWindow] = []

        def callback(hwnd, _extra) -> None:
            try:
                window = self._window_from_hwnd(hwnd)
            except Exception:
                return
            if window and self._is_candidate(window):
                windows.append(window)

        win32gui.EnumWindows(callback, None)
        windows.sort(key=self._score, reverse=True)
        return windows

    def best_chat_window(self) -> WeChatWindow | None:
        windows = self.find_chat_windows()
        selected_hwnd = int(self.config.auto_window_ocr.selected_window_hwnd or 0)
        if selected_hwnd:
            for window in windows:
                if window.hwnd == selected_hwnd:
                    return window
        selected_title = self.config.auto_window_ocr.selected_window_title.strip()
        if selected_title:
            for window in windows:
                if window.title == selected_title:
                    return window
            for window in windows:
                if selected_title in window.title:
                    return window
        return windows[0] if windows else None

    def chat_area_rect(self, window: WeChatWindow) -> tuple[int, int, int, int] | None:
        cfg = self.config.auto_window_ocr
        left = window.rect[0] + max(0, cfg.crop_left)
        top = window.rect[1] + max(0, cfg.crop_top)
        right = window.rect[2] - max(0, cfg.crop_right)
        bottom = window.rect[3] - max(0, cfg.crop_bottom)
        if right - left < 80 or bottom - top < 80:
            self.logger.warning("自动窗口 OCR 裁剪区域过小：window=%s bbox=%s", window.rect, (left, top, right, bottom))
            return None
        return (left, top, right, bottom)

    def _window_from_hwnd(self, hwnd: int) -> WeChatWindow | None:
        if not win32gui.IsWindowVisible(hwnd):
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = psutil.Process(pid).name()
        allowed_processes = {
            item.strip().lower()
            for item in [*self.config.auto_window_ocr.process_names, self.config.wechat.process_name]
            if item.strip()
        }
        if process_name.lower() not in allowed_processes:
            return None
        rect = win32gui.GetWindowRect(hwnd)
        title = win32gui.GetWindowText(hwnd) or ""
        class_name = win32gui.GetClassName(hwnd) or ""
        minimized = bool(win32gui.IsIconic(hwnd))
        return WeChatWindow(
            hwnd=int(hwnd),
            title=title,
            class_name=class_name,
            rect=tuple(int(value) for value in rect),
            process_name=process_name,
            minimized=minimized,
        )

    def _is_candidate(self, window: WeChatWindow) -> bool:
        cfg = self.config.auto_window_ocr
        if window.minimized:
            return False
        if window.width < cfg.min_width or window.height < cfg.min_height:
            return False
        class_names = [item.strip().lower() for item in cfg.window_class_names if item.strip()]
        if window.class_name.lower() in class_names:
            return True
        keywords = [item.strip().lower() for item in cfg.window_title_keywords if item.strip()]
        return bool(window.title and any(keyword in window.title.lower() for keyword in keywords))

    @staticmethod
    def _score(window: WeChatWindow) -> int:
        score = min(window.area // 10000, 80)
        if window.title and window.title not in {"微信", "WeChat"}:
            score += 120
        if window.class_name in {"WeChatMainWndForPC", "mmui::MainWindow", "Qt51514QWindowIcon"}:
            score += 50
        return score
