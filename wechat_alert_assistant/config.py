from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .assets import ALARM_AUDIO_PRESETS


CONFIG_PATH = Path("config.json")


@dataclass(slots=True)
class AppConfig:
    poll_interval_ms: int = 300
    alarm_sound: str = "preset:reflection"
    escalation_minutes: int = 3
    keep_awake_enabled: bool = True
    keep_awake_interval_seconds: int = 240
    keep_awake_simulate_input: bool = True
    keep_awake_mouse_nudge: bool = True
    debug_save_screenshots: bool = False
    log_ocr_text: bool = True
    cleanup_screenshots_keep: int = 80
    enable_tray: bool = False
    cross_source_dedup_seconds: int = 12


@dataclass(slots=True)
class WeChatConfig:
    process_name: str = "WeChat.exe"
    enable_popup_ocr: bool = False
    min_width: int = 180
    max_width: int = 520
    min_height: int = 80
    max_height: int = 260


@dataclass(slots=True)
class RegionConfig:
    enabled: bool = False
    poll_interval_seconds: int = 30
    bbox: list[int] = field(default_factory=list)
    require_chat_match: bool = False
    max_recent_lines: int = 12
    dedup_seconds: int = 180
    dedup_similarity: float = 0.88
    skip_existing_on_start: bool = True


@dataclass(slots=True)
class AutoWindowOcrConfig:
    enabled: bool = False
    poll_interval_seconds: int = 3
    process_names: list[str] = field(default_factory=lambda: ["Weixin.exe", "WeChat.exe"])
    selected_window_hwnd: int = 0
    selected_window_title: str = ""
    window_class_names: list[str] = field(
        default_factory=lambda: ["WeChatMainWndForPC", "mmui::MainWindow", "Qt51514QWindowIcon"]
    )
    window_title_keywords: list[str] = field(default_factory=lambda: ["微信", "WeChat"])
    min_width: int = 500
    min_height: int = 500
    crop_left: int = 300
    crop_top: int = 80
    crop_right: int = 20
    crop_bottom: int = 160
    require_chat_match: bool = False
    max_recent_lines: int = 12
    dedup_seconds: int = 180
    dedup_similarity: float = 0.88
    skip_existing_on_start: bool = True


@dataclass(slots=True)
class RulesConfig:
    monitored_chats: list[str] = field(default_factory=lambda: ["运维值班群", "项目A群"])
    my_names: list[str] = field(default_factory=lambda: ["@张三", "@三哥"])
    keywords: list[str] = field(default_factory=lambda: ["报警", "宕机", "P0", "紧急", "线上故障", "支付失败"])
    fuzzy_chat_threshold: float = 0.85
    fuzzy_name_threshold: float = 0.88
    allow_keyword_without_chat: bool = False


@dataclass(slots=True)
class NotificationConfig:
    contact_name: str = "李四"
    retry_attempts: int = 2
    retry_delay_seconds: int = 60
    automation_input_pos: list[int] = field(default_factory=list)
    automation_press_enter: bool = True
    automation_message_template: str = (
        "微信强提醒未处理，请电话提醒我。\n"
        "群：{chat_name}\n"
        "内容：{content}\n"
        "时间：{time}"
    )


@dataclass(slots=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    auto_window_ocr: AutoWindowOcrConfig = field(default_factory=AutoWindowOcrConfig)
    region: RegionConfig = field(default_factory=RegionConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)


def _dataclass_from_dict(cls: type, data: dict[str, Any]):
    field_names = {name for name in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    return cls(**{name: value for name, value in data.items() if name in field_names})


def config_from_dict(data: dict[str, Any]) -> Config:
    app = _dataclass_from_dict(AppConfig, data.get("app", {}))
    if str(app.alarm_sound).startswith("preset:") and app.alarm_sound not in ALARM_AUDIO_PRESETS:
        app.alarm_sound = "preset:reflection"
    return Config(
        app=app,
        wechat=_dataclass_from_dict(WeChatConfig, data.get("wechat", {})),
        auto_window_ocr=_dataclass_from_dict(AutoWindowOcrConfig, data.get("auto_window_ocr", {})),
        region=_dataclass_from_dict(RegionConfig, data.get("region", {})),
        rules=_dataclass_from_dict(RulesConfig, data.get("rules", {})),
        notification=_dataclass_from_dict(NotificationConfig, data.get("notification", {})),
    )


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        config = Config()
        save_config(config, path)
        return config
    with path.open("r", encoding="utf-8") as fp:
        return config_from_dict(json.load(fp))


def save_config(config: Config, path: Path = CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8") as fp:
        json.dump(asdict(config), fp, ensure_ascii=False, indent=2)
