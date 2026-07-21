from __future__ import annotations

import unittest

from wechat_alert_assistant.config import RulesConfig
from wechat_alert_assistant.rules import match_region_rules, matched_rule_items, parse_message


class RuleMatchingTests(unittest.TestCase):
    def test_at_name_tolerates_small_ocr_variation(self) -> None:
        rules = RulesConfig(my_names=["@SI YoneFeng"], keywords=[])
        message = parse_message("消息测试\n@Sl YoneFeng 请处理")

        result = match_region_rules(message, rules)

        self.assertTrue(result.matched)
        self.assertEqual("@me", result.reason)
        self.assertEqual(["@SI YoneFeng"], matched_rule_items(message, rules))

    def test_unrelated_name_does_not_match(self) -> None:
        rules = RulesConfig(my_names=["@SI YoneFeng"], keywords=[])
        message = parse_message("消息测试\n@other 请处理")

        result = match_region_rules(message, rules)

        self.assertFalse(result.matched)


if __name__ == "__main__":
    unittest.main()
