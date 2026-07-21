from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4


AlertStatus = Literal["pending", "resolved", "delayed", "escalated", "ignored", "failed"]


def now() -> datetime:
    return datetime.now()


def make_id(prefix: str) -> str:
    return f"{prefix}_{now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"


@dataclass(slots=True)
class Message:
    chat_name: str
    sender: str
    content: str
    raw_text: str
    source: str = "wechat_popup_ocr"
    created_at: datetime = field(default_factory=now)
    id: str = field(default_factory=lambda: make_id("msg"))


@dataclass(slots=True)
class Alert:
    message: Message
    trigger_reason: str
    escalation_minutes: int
    status: AlertStatus = "pending"
    created_at: datetime = field(default_factory=now)
    id: str = field(default_factory=lambda: make_id("alert"))
    notified_contact: bool = False
    notify_inflight: bool = False
    notification_attempts: int = 0
    next_notify_at: datetime | None = None
    last_notify_error: str = ""
    resolved_at: datetime | None = None

    @property
    def deadline_at(self) -> datetime:
        return self.created_at + timedelta(minutes=self.escalation_minutes)

    def delay(self, minutes: int) -> None:
        self.created_at = self.created_at + timedelta(minutes=minutes)
        self.status = "delayed"

    def resolve(self) -> None:
        self.status = "resolved"
        self.notify_inflight = False
        self.next_notify_at = None
        self.resolved_at = now()

    def escalate(self) -> None:
        self.status = "escalated"
        self.notified_contact = True
        self.notify_inflight = False
        self.next_notify_at = None

    def begin_notification(self) -> int:
        self.notification_attempts += 1
        self.notify_inflight = True
        self.next_notify_at = None
        return self.notification_attempts

    def schedule_notification_retry(self, error: str, retry_delay_seconds: int, current: datetime | None = None) -> None:
        self.status = "failed"
        self.notified_contact = False
        self.notify_inflight = False
        self.last_notify_error = error
        self.next_notify_at = (current or now()) + timedelta(seconds=max(1, retry_delay_seconds))

    def fail_notification_permanently(self, error: str) -> None:
        self.status = "failed"
        self.notified_contact = False
        self.notify_inflight = False
        self.last_notify_error = error
        self.next_notify_at = None

    def can_retry_notification(self, max_attempts: int) -> bool:
        return (
            self.status == "failed"
            and not self.notified_contact
            and not self.notify_inflight
            and self.notification_attempts < max(1, max_attempts)
        )

    def notification_retry_due(self, max_attempts: int, current: datetime | None = None) -> bool:
        if not self.can_retry_notification(max_attempts):
            return False
        if self.next_notify_at is None:
            return True
        return (current or now()) >= self.next_notify_at
