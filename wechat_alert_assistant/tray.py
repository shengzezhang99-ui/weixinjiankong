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

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            import pystray
            from PIL import Image
        except Exception as exc:  # noqa: BLE001
            self.logger.info("托盘不可用：%s", exc)
            return

        if APP_ICON_PNG.exists():
            image = Image.open(APP_ICON_PNG).convert("RGBA").resize((64, 64))
        else:
            image = Image.new("RGB", (64, 64), "#2563EB")

        def item(action: str):
            return lambda _icon, _item: self.dispatch(action)

        self._icon = pystray.Icon(
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
        self._thread = threading.Thread(target=self._run_icon, daemon=True)
        self._thread.start()
        self.logger.info("托盘图标已启动")

    def _run_icon(self) -> None:
        try:
            self._icon.run()
        except Exception:
            self.logger.exception("托盘图标运行异常")

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                self.logger.debug("停止托盘图标失败", exc_info=True)
        self._icon = None
