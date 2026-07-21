from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

ALARM_AUDIO_PRESETS: dict[str, tuple[str, Path]] = {
    "preset:reflection": ("倒影", PROJECT_ROOT / "audio" / "苹果倒影-原版-Reflection.mp3"),
    "preset:surge": ("风暴", PROJECT_ROOT / "audio" / "苹果倒影-风暴-Surge.mp3"),
    "preset:dreamer": ("梦想家", PROJECT_ROOT / "audio" / "苹果倒影-梦想家-Dreamer.mp3"),
}

APP_ICON_PNG = PROJECT_ROOT / "icon" / "5ttetm0toqg3foaw3josy874pajeqyi.png"
APP_ICON_ICO = PROJECT_ROOT / "icon" / "app.ico"
OCR_MODELS_ROOT = PROJECT_ROOT / "paddleocr_models"

OCR_MODEL_DIRS = {
    "det_model_dir": OCR_MODELS_ROOT / "det" / "ch" / "ch_PP-OCRv4_det_infer",
    "rec_model_dir": OCR_MODELS_ROOT / "rec" / "ch" / "ch_PP-OCRv4_rec_infer",
    "cls_model_dir": OCR_MODELS_ROOT / "cls" / "ch_ppocr_mobile_v2.0_cls_infer",
}


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _complete_model_dir(path: Path) -> bool:
    return (path / "inference.pdmodel").exists() and (path / "inference.pdiparams").exists()


def _ascii_cache_root() -> Path | None:
    candidates = [
        Path(os.environ["LOCALAPPDATA"]) / "WechatAlertAssistant"
        if os.environ.get("LOCALAPPDATA")
        else None,
        Path(os.environ["TEMP"]) / "WechatAlertAssistant" if os.environ.get("TEMP") else None,
        Path(os.environ["TMP"]) / "WechatAlertAssistant" if os.environ.get("TMP") else None,
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "WechatAlertAssistant",
    ]
    for candidate in candidates:
        if candidate is None or not _is_ascii_path(candidate):
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            continue
    return None


def resolve_ocr_model_dirs() -> dict[str, str]:
    if not all(_complete_model_dir(path) for path in OCR_MODEL_DIRS.values()):
        return {}
    if _is_ascii_path(OCR_MODELS_ROOT):
        return {key: str(path) for key, path in OCR_MODEL_DIRS.items()}

    cache_root = _ascii_cache_root()
    if cache_root is None:
        return {}

    cache_models_root = cache_root / "paddleocr_models"
    cache_dirs = {
        "det_model_dir": cache_models_root / "det" / "ch" / "ch_PP-OCRv4_det_infer",
        "rec_model_dir": cache_models_root / "rec" / "ch" / "ch_PP-OCRv4_rec_infer",
        "cls_model_dir": cache_models_root / "cls" / "ch_ppocr_mobile_v2.0_cls_infer",
    }
    try:
        for key, source in OCR_MODEL_DIRS.items():
            target = cache_dirs[key]
            if _complete_model_dir(target):
                continue
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
    except Exception:
        return {}

    if not all(_complete_model_dir(path) for path in cache_dirs.values()):
        return {}
    return {key: str(path) for key, path in cache_dirs.items()}


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
