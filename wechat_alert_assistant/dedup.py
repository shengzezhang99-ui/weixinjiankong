from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from .text_utils import compact_text


@dataclass(slots=True)
class _SeenLine:
    text: str
    seen_at: datetime


class OcrLineDeduplicator:
    """Tracks OCR lines independently for each capture source."""

    def __init__(self) -> None:
        self._seen_by_source: dict[str, list[_SeenLine]] = {}

    def new_lines(
        self,
        source: str,
        lines: list[str],
        current: datetime,
        retention_seconds: int,
        similarity_threshold: float,
    ) -> list[str]:
        seen = self._prune(source, current, retention_seconds)
        comparison = list(seen)
        result: list[str] = []
        for line in lines:
            normalized = _line_key(line)
            if len(normalized) < 2:
                continue
            if any(_similar_line(normalized, item.text, similarity_threshold) for item in comparison):
                continue
            result.append(line)
            comparison.append(_SeenLine(normalized, current))
        return result

    def mark_lines(
        self,
        source: str,
        lines: list[str],
        current: datetime,
        retention_seconds: int,
        similarity_threshold: float,
    ) -> None:
        seen = self._prune(source, current, retention_seconds)
        for line in lines:
            normalized = _line_key(line)
            if len(normalized) < 2:
                continue
            matched = next(
                (item for item in seen if _similar_line(normalized, item.text, similarity_threshold)),
                None,
            )
            if matched is not None:
                matched.seen_at = current
            else:
                seen.append(_SeenLine(normalized, current))

    def _prune(self, source: str, current: datetime, retention_seconds: int) -> list[_SeenLine]:
        cutoff = current - timedelta(seconds=max(1, retention_seconds))
        seen = [item for item in self._seen_by_source.get(source, []) if item.seen_at >= cutoff]
        self._seen_by_source[source] = seen
        return seen


@dataclass(slots=True)
class _SeenAlertEvent:
    source: str
    reason: str
    evidence: tuple[str, ...]
    relevant_lines: tuple[str, ...]
    seen_at: datetime


class CrossSourceAlertDeduplicator:
    """Suppresses the same OCR event observed by two monitor modes."""

    def __init__(self) -> None:
        self._events: list[_SeenAlertEvent] = []

    def check_and_mark(
        self,
        source: str,
        reason: str,
        raw_text: str,
        evidence: list[str],
        current: datetime,
        retention_seconds: int,
        similarity_threshold: float,
    ) -> bool:
        cutoff = current - timedelta(seconds=max(1, retention_seconds))
        self._events = [item for item in self._events if item.seen_at >= cutoff]

        evidence_keys = tuple(sorted({_line_key(item) for item in evidence if _line_key(item)}))
        relevant_lines = tuple(_relevant_lines(raw_text, evidence_keys, similarity_threshold))
        duplicate = any(
            item.source != source
            and item.reason == reason
            and bool(set(item.evidence) & set(evidence_keys))
            and _line_sets_overlap(item.relevant_lines, relevant_lines)
            for item in self._events
        )
        if not duplicate:
            self._events.append(
                _SeenAlertEvent(source, reason, evidence_keys, relevant_lines, current)
            )
        return duplicate


def _line_key(text: str) -> str:
    return compact_text(text).casefold()


def _similar_line(left: str, right: str, threshold: float) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 4:
        return False
    return SequenceMatcher(None, left, right).ratio() >= max(0.0, min(1.0, threshold))


def _relevant_lines(raw_text: str, evidence: tuple[str, ...], threshold: float) -> list[str]:
    result: list[str] = []
    for line in raw_text.splitlines():
        key = _line_key(line)
        if not key or _is_time_line(key):
            continue
        canonical = key
        matched = False
        for item in evidence:
            canonical_item = _canonicalize_evidence(canonical, item, threshold)
            if canonical_item is not None:
                canonical = canonical_item
                matched = True
        if matched:
            result.append(canonical)
    return result


def _canonicalize_evidence(text: str, evidence: str, threshold: float) -> str | None:
    if not evidence:
        return None
    if evidence in text:
        return text
    if len(text) < len(evidence):
        return evidence if _similar_line(text, evidence, threshold) else None

    width = len(evidence)
    best_index = -1
    best_ratio = 0.0
    for index in range(len(text) - width + 1):
        ratio = SequenceMatcher(None, text[index : index + width], evidence).ratio()
        if ratio > best_ratio:
            best_index = index
            best_ratio = ratio
    if best_index < 0 or best_ratio < threshold:
        return None
    return text[:best_index] + evidence + text[best_index + width :]


def _line_sets_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return bool(set(left) & set(right))


def _is_time_line(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", text))
