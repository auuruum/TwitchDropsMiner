import asyncio
import logging
from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from exceptions import CaptchaRequired
from nicegui import app, ui
import settings as settings_module
import truststore
from webui import apprise_notifier, auth, ssl
from webui.farm_status import FarmStatusNotifier


@pytest.mark.parametrize("failure", ["fatal", "captcha", "initialization", "shutdown", "normal"])
def test_backend_failure_notifies_once_and_shutdown_flushes_it(monkeypatch, failure):
    callbacks = {}
    delivered = []
    settings = SimpleNamespace(
        language="English", logging_level=logging.ERROR, log=False, stdlog=False,
        debug_ws=0, debug_gql=0, apprise_urls=["test://local"],
    )
    monkeypatch.setattr(settings_module, "Settings", lambda args: settings)
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: None)
    monkeypatch.setattr(auth, "AuthManager", lambda: None)
    monkeypatch.setattr(ssl, "get_ssl_kwargs", lambda path: {})
    monkeypatch.setattr(logging.getLogger(), "handlers", list(logging.getLogger().handlers))
    monkeypatch.setattr(
        apprise_notifier, "_send_sync",
        lambda urls, title, body: delivered.append((title, body)),
    )

    class FakeTwitch:
        def __init__(self, settings):
            if failure == "initialization":
                raise ValueError("private initialization details")
            self.settings = settings
            self.watching_channel = SimpleNamespace(get_with_default=lambda default: None)
            self.gui = SimpleNamespace(
                close_requested=False, notifications=FarmStatusNotifier(self),
                tray=MagicMock(), status=MagicMock(), grab_attention=MagicMock(),
                wait_until_closed=AsyncMock(), stop=MagicMock(),
                close_window=MagicMock(), close=MagicMock(),
            )
            self.print = MagicMock()
            self.prevent_close = MagicMock()
            self.save = MagicMock()

        async def run(self):
            if failure == "fatal":
                raise ValueError("private runtime details")
            if failure == "captcha":
                raise CaptchaRequired()
            if failure == "normal":
                self.gui.close_requested = True

        async def shutdown(self):
            if failure == "shutdown":
                raise ValueError("private shutdown details")

    twitch_module = ModuleType("twitch")
    twitch_module.Twitch = FakeTwitch
    monkeypatch.setitem(sys.modules, "twitch", twitch_module)
    monkeypatch.setitem(sys.modules, "gui", ModuleType("gui"))
    monkeypatch.setitem(sys.modules, "webui.patches", ModuleType("webui.patches"))

    def on_startup(callback):
        callbacks["startup"] = callback
        return callback

    def on_shutdown(callback):
        callbacks["shutdown"] = callback
        return callback

    monkeypatch.setattr(app, "on_startup", on_startup)
    monkeypatch.setattr(app, "on_shutdown", on_shutdown)
    monkeypatch.setattr(app, "get", lambda path: lambda callback: callback)
    monkeypatch.setattr(app, "shutdown", MagicMock())

    def run_ui(**kwargs):
        async def exercise_backend():
            await callbacks["startup"]()
            pending = asyncio.all_tasks() - {asyncio.current_task()}
            await asyncio.gather(*pending)
            await callbacks["shutdown"]()
        asyncio.run(exercise_backend())

    monkeypatch.setattr(ui, "run", run_ui)
    import utils
    monkeypatch.setattr(utils, "lock_file", lambda path: (True, SimpleNamespace(close=lambda: None)))
    entrypoint = Path(__file__).resolve().parents[1] / "main_webui.py"
    monkeypatch.setattr(sys, "argv", [str(entrypoint)])

    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(entrypoint), run_name="__main__")

    assert result.value.code == (0 if failure == "normal" else 1)
    assert len(delivered) == (0 if failure == "normal" else 1)
    if delivered:
        title, body = delivered[0]
        assert title == "Miner Error"
        assert "git pull" in body
        assert "private" not in body
