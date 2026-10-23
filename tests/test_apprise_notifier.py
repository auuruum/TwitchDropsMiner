import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from webui import apprise_notifier as notifier


def test_shutdown_waits_for_queued_notification(monkeypatch):
    delivered = []

    async def send(urls, title, body):
        await asyncio.sleep(0)
        delivered.append((title, body))

    monkeypatch.setattr(notifier, "_send_async", send)

    async def run():
        notifier.send_apprise(SimpleNamespace(apprise_urls=["test://local"]), "Title", "Body")
        await notifier.flush_notifications()
        assert not notifier._pending_notifications

    asyncio.run(run())
    assert delivered == [("Title", "Body\nChannel: not watching")]


def test_provider_exception_does_not_leak_credentials_or_raise(monkeypatch, caplog):
    provider = MagicMock()
    provider.notify.side_effect = RuntimeError("secret-webhook-url")
    monkeypatch.setitem(sys.modules, "apprise", SimpleNamespace(Apprise=lambda: provider))
    notifier._send_sync(["test://local"], "Title", "Body")
    assert "RuntimeError" in caplog.text
    assert "secret-webhook-url" not in caplog.text


def test_error_contains_actionable_recovery_steps(monkeypatch):
    send = MagicMock()
    monkeypatch.setattr(notifier, "send_apprise", send)
    notifier.send_miner_error(SimpleNamespace(), "Backend failed (ValueError).")
    title, body = send.call_args.args[1:]
    assert title == "Miner Error"
    assert all(text in body for text in ("logs", "git pull", "requirements-nicegui.txt", "restart"))
