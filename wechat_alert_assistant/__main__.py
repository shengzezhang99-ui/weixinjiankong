from __future__ import annotations

import os
import sys
from multiprocessing import freeze_support

from .flet_ui import run
from .logging_setup import setup_logging
from .monitor import OcrEngine


if __name__ == "__main__":
    freeze_support()
    if os.environ.get("WECHAT_ALERT_ASSISTANT_OCR_SMOKE") == "1":
        logger = setup_logging()
        engine = OcrEngine(logger)
        available = engine.available
        print(f"ocr_available={available}")
        if engine.last_error:
            print(f"ocr_last_error={engine.last_error}")
        sys.exit(0 if available else 2)
    run()
