from __future__ import annotations

import unittest

from wechat_alert_assistant.assets import ALARM_AUDIO_PRESETS, APP_ICON_PNG, alarm_preset_options, ensure_app_icon_ico
from wechat_alert_assistant.config import Config


class AlarmPresetTests(unittest.TestCase):
    def test_builtin_alarm_presets_are_available(self) -> None:
        options = dict(alarm_preset_options())

        self.assertEqual("倒影", options["preset:reflection"])
        self.assertEqual("风暴", options["preset:surge"])
        self.assertEqual("梦想家", options["preset:dreamer"])
        self.assertTrue(all(path.exists() for _name, path in ALARM_AUDIO_PRESETS.values()))

    def test_default_alarm_uses_builtin_preset(self) -> None:
        self.assertEqual("preset:reflection", Config().app.alarm_sound)

    def test_application_icon_exists(self) -> None:
        self.assertTrue(APP_ICON_PNG.exists())
        icon_path = ensure_app_icon_ico()
        self.assertIsNotNone(icon_path)
        self.assertTrue(icon_path.exists())


if __name__ == "__main__":
    unittest.main()
