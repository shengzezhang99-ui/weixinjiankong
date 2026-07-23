from __future__ import annotations

import atexit
import logging
import threading
import time
import winsound
from pathlib import Path

from .assets import resolve_alarm_sound


class AlarmPlayer:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        atexit.register(self.stop)

    def start(self, sound_path: str = "") -> None:
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(sound_path,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._stop_pygame()
        self._stop_winsound()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)
        if self._thread is thread:
            self._thread = None

    def _run(self, sound_path: str) -> None:
        path = resolve_alarm_sound(sound_path)
        if path and path.exists():
            if self._play_with_pygame(path):
                return
            if path.suffix.lower() == ".wav" and self._play_wav_with_winsound(path):
                return

        while not self._stop.is_set():
            winsound.MessageBeep(winsound.MB_ICONHAND)
            time.sleep(0.8)

    def _play_with_pygame(self, path: Path) -> bool:
        try:
            import pygame

            pygame.mixer.init()
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play(loops=-1)
            try:
                while not self._stop.is_set():
                    time.sleep(0.2)
            finally:
                self._stop_pygame()
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("pygame 播放报警音失败，尝试回退：%s", exc)
            self._stop_pygame()
            return False

    def _play_wav_with_winsound(self, path: Path) -> bool:
        try:
            winsound.PlaySound(
                str(path),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
            )
            while not self._stop.is_set():
                time.sleep(0.2)
            self._stop_winsound()
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("winsound 播放报警音失败：%s", exc)
            self._stop_winsound()
            return False

    @staticmethod
    def _stop_winsound() -> None:
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    @staticmethod
    def _stop_pygame() -> None:
        try:
            import pygame

            if pygame.mixer.get_init():
                try:
                    pygame.mixer.music.stop()
                finally:
                    pygame.mixer.quit()
        except Exception:
            pass
