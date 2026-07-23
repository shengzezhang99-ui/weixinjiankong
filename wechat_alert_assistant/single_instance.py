from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
import threading
from collections.abc import Callable


ERROR_ALREADY_EXISTS = 183
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000

MUTEX_NAME = "Local\\WechatAlertAssistantSingleInstance"
SHOW_EVENT_NAME = "Local\\WechatAlertAssistantShowMainWindow"


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class SingleInstance:
    def __init__(self) -> None:
        self._mutex = None
        self._event = None
        self._listener_thread: threading.Thread | None = None
        self._running = False

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._mutex = handle
        self._event = self._create_event()
        return True

    def signal_existing(self) -> bool:
        if os.name != "nt":
            return False
        kernel32 = _kernel32()
        access = EVENT_MODIFY_STATE | SYNCHRONIZE
        handle = kernel32.OpenEventW(access, False, SHOW_EVENT_NAME)
        if not handle:
            handle = self._create_event()
        if not handle:
            return False
        try:
            return bool(kernel32.SetEvent(handle))
        finally:
            kernel32.CloseHandle(handle)

    def start_show_listener(self, callback: Callable[[], None], logger: logging.Logger) -> None:
        if os.name != "nt" or self._listener_thread:
            return
        if not self._event:
            self._event = self._create_event()
        if not self._event:
            logger.warning("单实例唤醒事件创建失败")
            return
        self._running = True

        def listen() -> None:
            kernel32 = _kernel32()
            logger.info("单实例唤醒监听已启动")
            while self._running:
                result = kernel32.WaitForSingleObject(self._event, 500)
                if result == WAIT_OBJECT_0:
                    if not self._running:
                        break
                    try:
                        logger.info("收到第二实例唤醒请求")
                        callback()
                    except Exception:
                        logger.exception("处理第二实例唤醒请求失败")
                elif result not in (WAIT_TIMEOUT,):
                    logger.warning("单实例唤醒监听异常 result=%s", result)
                    break

        self._listener_thread = threading.Thread(target=listen, name="wechat-alert-single-instance", daemon=True)
        self._listener_thread.start()

    def close(self) -> None:
        if os.name != "nt":
            return
        kernel32 = _kernel32()
        self._running = False
        if self._event:
            try:
                kernel32.SetEvent(self._event)
            except Exception:
                pass
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=1)
        if self._mutex:
            try:
                kernel32.ReleaseMutex(self._mutex)
            except Exception:
                pass
            kernel32.CloseHandle(self._mutex)
            self._mutex = None
        if self._event:
            kernel32.CloseHandle(self._event)
            self._event = None

    @staticmethod
    def _create_event():
        kernel32 = _kernel32()
        handle = kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
        if not handle:
            return None
        return handle
