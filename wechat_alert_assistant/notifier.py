from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import NotificationConfig
from .models import Alert


@dataclass(slots=True)
class NotifyResult:
    ok: bool
    message: str


class AutomationNotifier:
    def __init__(self, config: NotificationConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def send(self, alert: Alert) -> NotifyResult:
        if len(self.config.automation_input_pos) != 2:
            return NotifyResult(False, "模拟点击通知未配置输入框坐标")
        try:
            import pyautogui
            import pyperclip
        except Exception as exc:  # noqa: BLE001
            return NotifyResult(False, f"缺少模拟点击依赖 pyautogui/pyperclip：{exc}")

        msg = alert.message
        text = self.config.automation_message_template.format(
            contact_name=self.config.contact_name,
            chat_name=msg.chat_name or "未识别",
            content=msg.content or msg.raw_text,
            raw_text=msg.raw_text,
            time=msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            trigger_reason=alert.trigger_reason,
        )

        x, y = self.config.automation_input_pos
        try:
            pyautogui.FAILSAFE = True
            pyautogui.click(x, y)
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            if self.config.automation_press_enter:
                pyautogui.press("enter")
            return NotifyResult(True, "模拟点击通知已执行")
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Automation notification failed")
            return NotifyResult(False, f"模拟点击通知失败：{exc}")


def send_notification(alert: Alert, config: NotificationConfig, logger: logging.Logger) -> NotifyResult:
    return AutomationNotifier(config, logger).send(alert)
