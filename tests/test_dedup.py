from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from wechat_alert_assistant.dedup import CrossSourceAlertDeduplicator, OcrLineDeduplicator


class OcrLineDeduplicatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = datetime(2026, 7, 21, 12, 0, 0)
        self.dedup = OcrLineDeduplicator()

    def test_fuzzy_ocr_variation_is_not_new(self) -> None:
        self.dedup.mark_lines("auto", ["@SI YoneFeng"], self.current, 180, 0.88)

        result = self.dedup.new_lines(
            "auto",
            ["@Sl YoneFeng"],
            self.current + timedelta(seconds=10),
            180,
            0.88,
        )

        self.assertEqual([], result)

    def test_retention_is_isolated_per_source(self) -> None:
        self.dedup.mark_lines("region", ["生产环境报警"], self.current, 180, 0.88)
        self.dedup.mark_lines("auto", ["生产环境报警"], self.current, 30, 0.88)
        later = self.current + timedelta(seconds=40)

        auto_result = self.dedup.new_lines("auto", ["生产环境报警"], later, 30, 0.88)
        region_result = self.dedup.new_lines("region", ["生产环境报警"], later, 180, 0.88)

        self.assertEqual(["生产环境报警"], auto_result)
        self.assertEqual([], region_result)

    def test_duplicate_lines_in_one_snapshot_are_returned_once(self) -> None:
        result = self.dedup.new_lines(
            "region",
            ["支付失败", "支付失败"],
            self.current,
            180,
            0.88,
        )

        self.assertEqual(["支付失败"], result)

    def test_visible_lines_can_refresh_their_retention(self) -> None:
        self.dedup.mark_lines("auto", ["@SI YoneFeng"], self.current, 30, 0.88)
        middle = self.current + timedelta(seconds=20)
        self.assertEqual([], self.dedup.new_lines("auto", ["@SI YoneFeng"], middle, 30, 0.88))
        self.dedup.mark_lines("auto", ["@SI YoneFeng"], middle, 30, 0.88)

        result = self.dedup.new_lines(
            "auto",
            ["@SI YoneFeng"],
            self.current + timedelta(seconds=40),
            30,
            0.88,
        )

        self.assertEqual([], result)


class CrossSourceAlertDeduplicatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = datetime(2026, 7, 21, 12, 0, 0)
        self.dedup = CrossSourceAlertDeduplicator()

    def test_same_event_from_two_sources_is_suppressed(self) -> None:
        first = self.dedup.check_and_mark(
            "auto_window_ocr",
            "@me",
            "@SI YoneFeng",
            ["@SI YoneFeng"],
            self.current,
            12,
            0.88,
        )
        second = self.dedup.check_and_mark(
            "region_ocr",
            "@me",
            "@Sl YoneFeng",
            ["@SI YoneFeng"],
            self.current + timedelta(seconds=4),
            12,
            0.88,
        )

        self.assertFalse(first)
        self.assertTrue(second)

    def test_distinct_messages_with_same_at_name_are_preserved(self) -> None:
        self.dedup.check_and_mark(
            "auto_window_ocr",
            "@me",
            "@SI YoneFeng 处理报警A",
            ["@SI YoneFeng"],
            self.current,
            12,
            0.88,
        )

        duplicate = self.dedup.check_and_mark(
            "region_ocr",
            "@me",
            "@SI YoneFeng 处理报警B",
            ["@SI YoneFeng"],
            self.current + timedelta(seconds=4),
            12,
            0.88,
        )

        self.assertFalse(duplicate)

    def test_event_expires_after_cross_source_window(self) -> None:
        self.dedup.check_and_mark(
            "auto_window_ocr",
            "keyword",
            "支付失败",
            ["支付失败"],
            self.current,
            12,
            0.88,
        )

        duplicate = self.dedup.check_and_mark(
            "region_ocr",
            "keyword",
            "支付失败",
            ["支付失败"],
            self.current + timedelta(seconds=13),
            12,
            0.88,
        )

        self.assertFalse(duplicate)


if __name__ == "__main__":
    unittest.main()
