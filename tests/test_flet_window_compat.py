from __future__ import annotations

import unittest
from types import SimpleNamespace

from wechat_alert_assistant.flet_ui import FletAssistantApp


class _Logger:
    def debug(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass

    def info(self, *_args, **_kwargs) -> None:
        pass


class _PackagedLikePage:
    def __init__(self) -> None:
        self.window = None
        self.attrs: dict[str, object] = {}
        self.handlers: dict[str, object] = {}
        self.updated = False

    def __setattr__(self, name: str, value) -> None:
        if name.startswith("window_") or name == "on_window_event":
            raise AttributeError("'NoneType' object has no attribute 'handler'")
        super().__setattr__(name, value)

    def _set_attr(self, name: str, value) -> None:
        self.attrs[name] = value

    def _add_event_handler(self, name: str, handler) -> None:
        self.handlers[name] = handler

    def update(self) -> None:
        self.updated = True


class FletWindowCompatTests(unittest.TestCase):
    def _app_with_page(self, page):
        app = FletAssistantApp.__new__(FletAssistantApp)
        app.page = page
        app.logger = _Logger()
        return app

    def test_window_state_falls_back_to_raw_flet_attrs(self) -> None:
        page = _PackagedLikePage()
        app = self._app_with_page(page)

        self.assertTrue(app._set_window_state("prevent_close", True))

        self.assertEqual(page.attrs["windowPreventClose"], True)

    def test_window_event_binding_uses_raw_event_when_window_is_missing(self) -> None:
        page = _PackagedLikePage()
        app = self._app_with_page(page)

        self.assertTrue(app._bind_window_event())

        handler = page.handlers["window_event"]
        self.assertIs(handler.__self__, app)
        self.assertIs(handler.__func__, FletAssistantApp.on_window_event)

    def test_close_window_falls_back_to_raw_flet_attrs(self) -> None:
        page = _PackagedLikePage()
        app = self._app_with_page(page)

        self.assertTrue(app._close_window())

        self.assertIn("windowDestroy", page.attrs)
        self.assertTrue(page.updated)

    def test_window_close_requests_full_exit_even_when_tray_is_enabled(self) -> None:
        app = self._app_with_page(_PackagedLikePage())
        app.exit_requested = False
        app.config = SimpleNamespace(app=SimpleNamespace(enable_tray=True))
        app.exit_was_requested = False

        def request_exit() -> None:
            app.exit_was_requested = True

        app.request_exit = request_exit

        app.on_window_event(SimpleNamespace(data="close"))

        self.assertTrue(app.exit_was_requested)

    def test_request_exit_releases_prevent_close_and_schedules_hard_exit(self) -> None:
        page = _PackagedLikePage()
        app = self._app_with_page(page)
        app.exit_requested = False
        app.closed = False
        app.running = True
        app.hard_exit_scheduled = False
        app.monitor = None
        app.alarm = SimpleNamespace(stop=lambda: None)
        app.keepalive = SimpleNamespace(stop=lambda: None)
        app.tray = SimpleNamespace(stop=lambda: None)
        app._terminate_flet_children = lambda: None
        app._schedule_hard_exit = lambda delay_seconds=2.0: setattr(app, "hard_exit_scheduled", True)

        app.request_exit()

        self.assertEqual(page.attrs["windowPreventClose"], False)
        self.assertEqual(page.attrs["windowAlwaysOnTop"], False)
        self.assertIn("windowDestroy", page.attrs)
        self.assertTrue(app.closed)
        self.assertTrue(app.hard_exit_scheduled)

    def test_alert_window_is_brought_to_front_and_topmost(self) -> None:
        page = _PackagedLikePage()
        app = self._app_with_page(page)
        app._restore_existing_window = lambda: True
        app._window_to_front = lambda: False

        app._bring_alert_window_to_front()

        self.assertEqual(page.attrs["windowVisible"], True)
        self.assertEqual(page.attrs["windowMinimized"], False)
        self.assertEqual(page.attrs["windowAlwaysOnTop"], True)
        self.assertEqual(page.attrs["windowFocused"], True)
        self.assertTrue(page.updated)


if __name__ == "__main__":
    unittest.main()
