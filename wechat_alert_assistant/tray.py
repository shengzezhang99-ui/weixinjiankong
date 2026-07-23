from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .assets import APP_ICON_PNG


class TrayController:
    def __init__(self, logger: logging.Logger, dispatch: Callable[[str], None]):
        self.logger = logger
        self.dispatch = dispatch
        self._icon = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
        try:
            import pystray
            from PIL import Image
        except Exception as exc:  # noqa: BLE001
            self.logger.info("托盘不可用：%s", exc)
            return

        if APP_ICON_PNG.exists():
            with Image.open(APP_ICON_PNG) as source:
                image = source.convert("RGBA").resize((64, 64))
        else:
            image = Image.new("RGB", (64, 64), "#2563EB")

        def item(action: str):
            def handle(_icon, _item) -> None:
                try:
                    self.logger.info("托盘菜单点击：%s", action)
                    self.dispatch(action)
                except Exception:
                    self.logger.exception("托盘菜单分发失败：%s", action)

            return handle

        icon = pystray.Icon(
            "wechat_alert_assistant",
            image,
            "微信强提醒助手",
            menu=pystray.Menu(
                pystray.MenuItem("打开主界面", item("show")),
                pystray.MenuItem("启动监控", item("start")),
                pystray.MenuItem("停止监控", item("stop")),
                pystray.MenuItem("测试报警", item("alarm")),
                pystray.MenuItem("退出", item("exit")),
            ),
        )
        thread = threading.Thread(target=self._run_icon, args=(icon,), name="wechat-alert-tray", daemon=True)
        with self._lock:
            self._icon = icon
            self._thread = thread
        thread.start()
        self.logger.info("托盘图标已启动")

    def _run_icon(self, icon) -> None:
        try:
            icon.run()
        except Exception:
            self.logger.exception("托盘图标运行异常")
        finally:
            with self._lock:
                if self._icon is icon:
                    self._icon = None
                    self._thread = None

    def stop(self) -> None:
        with self._lock:
            icon = self._icon
            thread = self._thread
            self._icon = None
            self._thread = None
        if icon:
            try:
                icon.stop()
            except Exception:
                self.logger.debug("停止托盘图标失败", exc_info=True)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)
