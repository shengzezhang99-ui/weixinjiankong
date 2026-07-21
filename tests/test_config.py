from __future__ import annotations

import unittest

from wechat_alert_assistant.config import config_from_dict


class ConfigCompatibilityTests(unittest.TestCase):
    def test_keep_awake_defaults_are_applied_to_old_config(self) -> None:
        config = config_from_dict({"app": {}, "notification": {}})

        self.assertTrue(config.app.keep_awake_enabled)
        self.assertEqual(240, config.app.keep_awake_interval_seconds)
        self.assertTrue(config.app.keep_awake_simulate_input)
        self.assertTrue(config.app.keep_awake_mouse_nudge)

    def test_legacy_notification_fields_are_ignored(self) -> None:
        config = config_from_dict(
            {
                "notification": {
                    "method": "removed-provider",
                    "removed_provider_token": "legacy-token",
                    "contact_name": "联系人",
                }
            }
        )

        self.assertEqual("联系人", config.notification.contact_name)
        self.assertFalse(hasattr(config.notification, "method"))
        self.assertFalse(hasattr(config.notification, "removed_provider_token"))

    def test_unknown_alarm_preset_migrates_to_reflection(self) -> None:
        config = config_from_dict({"app": {"alarm_sound": "preset:removed"}})

        self.assertEqual("preset:reflection", config.app.alarm_sound)


if __name__ == "__main__":
    unittest.main()
