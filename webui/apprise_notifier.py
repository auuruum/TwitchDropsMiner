from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from settings import Settings


logger = logging.getLogger("TwitchDrops")
_missing_apprise_logged = False


def send_apprise(settings: "Settings", title: str, body: str) -> None:
    urls = _apprise_urls(settings)
    if not urls:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _send_sync(urls, title, body)
    else:
        loop.create_task(_send_async(urls, title, body))


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

    notifier = apprise.Apprise()
    for url in urls:
        notifier.add(url)
    if not notifier.notify(title=title, body=body):
        logger.error("Apprise notification failed")
