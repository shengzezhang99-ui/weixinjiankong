from __future__ import annotations

import logging
import unittest

from wechat_alert_assistant.config import Config
from wechat_alert_assistant.monitor import WindowMonitor
from wechat_alert_assistant.rules import parse_message


class WindowMonitorDedupTests(unittest.TestCase):
    def test_emit_match_suppresses_equivalent_cross_source_event(self) -> None:
        emitted: list[tuple[str, str]] = []
        config = Config()
        config.rules.my_names = ["@SI YoneFeng"]
        monitor = WindowMonitor(
            config,
            logging.getLogger("test-monitor"),
            on_message=lambda message, reason: emitted.append((message.source, reason)),
        )
        auto_message = parse_message("@SI YoneFeng")
        auto_message.source = "auto_window_ocr"
        region_message = parse_message("@Sl YoneFeng")
        region_message.source = "region_ocr"

        first = monitor._emit_match(auto_message, "@me", 0.88)
        second = monitor._emit_match(region_message, "@me", 0.88)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual([("auto_window_ocr", "@me")], emitted)


if __name__ == "__main__":
    unittest.main()
