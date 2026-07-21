from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from wechat_alert_assistant.models import Alert, Message


class AlertNotificationRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = datetime(2026, 7, 21, 12, 0, 0)
        message = Message(chat_name="运维值班群", sender="张三", content="报警", raw_text="报警")
        self.alert = Alert(message=message, trigger_reason="keyword", escalation_minutes=3)

    def test_failed_notification_waits_until_retry_time(self) -> None:
        self.alert.begin_notification()
        self.alert.schedule_notification_retry("network error", 60, self.current)

        self.assertFalse(self.alert.notification_retry_due(3, self.current + timedelta(seconds=59)))
        self.assertTrue(self.alert.notification_retry_due(3, self.current + timedelta(seconds=60)))

    def test_retry_stops_after_max_attempts(self) -> None:
        self.alert.begin_notification()
        self.alert.schedule_notification_retry("first failure", 60, self.current)
        self.alert.begin_notification()
        self.alert.schedule_notification_retry("second failure", 60, self.current)
        self.alert.begin_notification()
        self.alert.fail_notification_permanently("third failure")

        self.assertFalse(self.alert.can_retry_notification(3))
        self.assertFalse(self.alert.notification_retry_due(3, self.current + timedelta(minutes=10)))
        self.assertIsNone(self.alert.next_notify_at)
        self.assertEqual("third failure", self.alert.last_notify_error)

    def test_resolve_clears_pending_retry(self) -> None:
        self.alert.begin_notification()
        self.alert.schedule_notification_retry("network error", 60, self.current)

        self.alert.resolve()

        self.assertFalse(self.alert.notify_inflight)
        self.assertIsNone(self.alert.next_notify_at)
        self.assertFalse(self.alert.can_retry_notification(3))


if __name__ == "__main__":
    unittest.main()
