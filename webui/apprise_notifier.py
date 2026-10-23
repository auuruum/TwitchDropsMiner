from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from settings import Settings


logger = logging.getLogger("TwitchDrops")
_missing_apprise_logged = False
_pending_notifications: set[asyncio.Task[None]] = set()
RECOVERY_HINT = (
    "Check the miner logs and Twitch login/network connection. If the issue persists, "
    "run git pull in the project folder, reinstall dependencies from "
    "requirements-nicegui.txt and restart the miner."
)


def send_apprise(settings: "Settings", title: str, body: str) -> asyncio.Task[None] | None:
    urls = _apprise_urls(settings)
    if not urls:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _send_sync(urls, title, body)
    else:
        task = loop.create_task(_send_async(urls, title, body))
        _pending_notifications.add(task)
        task.add_done_callback(_pending_notifications.discard)
        return task


def send_miner_error(settings: "Settings", message: str) -> None:
    send_apprise(settings, "Miner Error", message + "\n\n" + RECOVERY_HINT)


async def flush_notifications() -> None:
    if _pending_notifications:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tuple(_pending_notifications), return_exceptions=True),
                timeout=10,
            )
        except asyncio.TimeoutError:
            logger.error("Timed out waiting for Apprise notifications")


def _apprise_urls(settings: "Settings") -> list[str]:
    urls = getattr(settings, "apprise_urls", [])
    if not isinstance(urls, Sequence) or isinstance(urls, (str, bytes)):
        return []
    return [url for url in urls if isinstance(url, str) and url.strip()]


async def _send_async(urls: list[str], title: str, body: str) -> None:
    await asyncio.to_thread(_send_sync, urls, title, body)


def _send_sync(urls: list[str], title: str, body: str) -> None:
    global _missing_apprise_logged
    try:
        import apprise
    except ImportError:
        if not _missing_apprise_logged:
            logger.error(
                "Apprise notifications are configured, but apprise is not installed."
            )
            _missing_apprise_logged = True
        return

    try:
        notifier = apprise.Apprise()
        for url in urls:
            notifier.add(url)
        if not notifier.notify(title=title, body=body):
            logger.error("Apprise notification failed")
    except Exception as exc:
        # Provider exceptions may contain notification URLs and credentials.
        logger.error("Apprise notification failed (%s)", type(exc).__name__)
