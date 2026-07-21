from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

ALARM_AUDIO_PRESETS: dict[str, tuple[str, Path]] = {
    "preset:reflection": ("倒影", PROJECT_ROOT / "audio" / "苹果倒影-原版-Reflection.mp3"),
    "preset:surge": ("风暴", PROJECT_ROOT / "audio" / "苹果倒影-风暴-Surge.mp3"),
    "preset:dreamer": ("梦想家", PROJECT_ROOT / "audio" / "苹果倒影-梦想家-Dreamer.mp3"),
}

APP_ICON_PNG = PROJECT_ROOT / "icon" / "5ttetm0toqg3foaw3josy874pajeqyi.png"
APP_ICON_ICO = PROJECT_ROOT / "icon" / "app.ico"


def alarm_preset_options() -> list[tuple[str, str]]:
    return [(key, label) for key, (label, _path) in ALARM_AUDIO_PRESETS.items()]


def resolve_alarm_sound(value: str) -> Path | None:
    preset = ALARM_AUDIO_PRESETS.get(value)
    if preset:
        return preset[1]
    path = Path(value) if value else None
    return path if path else None


def ensure_app_icon_ico() -> Path | None:
    if not APP_ICON_PNG.exists():
        return None
    try:
        if APP_ICON_ICO.exists() and APP_ICON_ICO.stat().st_mtime >= APP_ICON_PNG.stat().st_mtime:
            return APP_ICON_ICO
        from PIL import Image

        image = Image.open(APP_ICON_PNG).convert("RGBA")
        image.save(APP_ICON_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return APP_ICON_ICO
    except Exception:
        return None
