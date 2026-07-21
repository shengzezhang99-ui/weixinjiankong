from __future__ import annotations

import ctypes
import logging
import threading
import time


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
VK_F15 = 0x7E


class KeepAliveController:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(
        self,
        interval_seconds: int = 240,
        simulate_input: bool = True,
        mouse_nudge: bool = True,
    ) -> None:
        self.stop()
        self._stop.clear()
        interval = max(30, int(interval_seconds))
        self._thread = threading.Thread(
            target=self._run,
            args=(interval, bool(simulate_input), bool(mouse_nudge)),
            daemon=True,
        )
        self._thread.start()
        self.logger.info(
            "电脑保活已启动 interval=%ss simulate_input=%s mouse_nudge=%s",
            interval,
            simulate_input,
            mouse_nudge,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._thread = None
        self._clear_execution_state()

    def _run(self, interval_seconds: int, simulate_input: bool, mouse_nudge: bool) -> None:
        while not self._stop.is_set():
            self._prevent_sleep()
            if simulate_input:
                self._send_f15_key()
            if mouse_nudge:
                self._nudge_mouse()
            if self._stop.wait(interval_seconds):
                break

    def _prevent_sleep(self) -> None:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint
            result = kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
            if result == 0:
                self.logger.warning("电脑保活刷新失败：SetThreadExecutionState 返回 0")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("电脑保活刷新失败：%s", exc)

    def _send_f15_key(self) -> None:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_ulong]
            user32.keybd_event.restype = None
            user32.keybd_event(VK_F15, 0, 0, 0)
            user32.keybd_event(VK_F15, 0, KEYEVENTF_KEYUP, 0)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("电脑保活按键刷新失败：%s", exc)

    def _nudge_mouse(self) -> None:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.mouse_event.argtypes = [ctypes.c_uint, ctypes.c_long, ctypes.c_long, ctypes.c_uint, ctypes.c_ulong]
            user32.mouse_event.restype = None
            user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("电脑保活鼠标微动失败：%s", exc)

    def _clear_execution_state(self) -> None:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass
