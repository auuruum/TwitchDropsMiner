import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from webui import apprise_notifier as notifier


@pytest.fixture
def delivery(monkeypatch):
    messages = []
    monkeypatch.setattr(notifier, "_send_sync", lambda urls, title, body: messages.append((title, body)))
    channel = SimpleNamespace(name="TwitchRivals", url="https://www.twitch.tv/twitchrivals", online=True)
    settings = SimpleNamespace(apprise_urls=["test://local"])
    return SimpleNamespace(messages=messages, channel=channel, settings=settings)


def test_channel_name_and_link_are_included(delivery):
    notifier.send_apprise(delivery.settings, "Progress", "35% (21/60 min)", channel=delivery.channel)
    assert delivery.messages == [(
        "Progress", "35% (21/60 min)\nChannel: TwitchRivals (https://www.twitch.tv/twitchrivals)",
    )]


@pytest.mark.parametrize("offline", [False, True])
def test_no_active_channel_is_explicit(delivery, offline):
    delivery.channel.online = False
    notifier.send_apprise(
        delivery.settings, "Ready", "Waiting", channel=delivery.channel if offline else None,
    )
    assert delivery.messages[0][1] == "Waiting\nChannel: not watching"


def test_queued_message_keeps_channel_at_time_of_event(delivery):
    async def run():
        notifier.send_apprise(delivery.settings, "Progress", "35%", channel=delivery.channel)
        delivery.channel.name = "AnotherStreamer"
        delivery.channel.url = "https://www.twitch.tv/anotherstreamer"
        notifier.send_apprise(delivery.settings, "Progress", "40%", channel=delivery.channel)
        await notifier.flush_notifications()

    asyncio.run(run())
    bodies = [body for _, body in delivery.messages]
    assert any("35%\nChannel: TwitchRivals (https://www.twitch.tv/twitchrivals)" == body for body in bodies)
    assert any("40%\nChannel: AnotherStreamer (https://www.twitch.tv/anotherstreamer)" == body for body in bodies)


def test_failure_includes_current_channel(delivery):
    notifier.send_miner_error(delivery.settings, "Backend failed.", channel=delivery.channel)
    title, body = delivery.messages[0]
    assert title == "Miner Error"
    assert "git pull" in body
    assert body.count("Channel:") == 1
    assert "TwitchRivals (https://www.twitch.tv/twitchrivals)" in body


@pytest.mark.parametrize("event", ["progress", "claimed", "campaign", "login"])
def test_adapters_pass_the_actual_watched_channel(delivery, monkeypatch, event):
    from translate import _
    from webui.adapters.inventory_overview import InventoryOverviewAdapter
    from webui.adapters.login_form import LoginFormAdapter
    from webui.components.main.drop_section import DropSection

    manager = SimpleNamespace(
        _twitch=SimpleNamespace(
            settings=delivery.settings,
            watching_channel=SimpleNamespace(get_with_default=lambda default: delivery.channel),
        ),
        main_panel=MagicMock(), inventory_panel=MagicMock(), status=MagicMock(),
    )
    campaign = SimpleNamespace(
        id="campaign", name="Campaign", game=SimpleNamespace(name="Rust"),
        active=True, claimed_drops=1, total_drops=2,
    )
    drop = SimpleNamespace(
        id="drop", campaign=campaign, is_claimed=event == "claimed",
        required_minutes=60, current_minutes=21, progress=0.35,
        rewards_text=lambda: "Rug",
    )
    if event == "progress":
        section = object.__new__(DropSection)
        section._manager = manager
        section._notified_progress = {}
        section._notify_progress(drop)
    elif event == "claimed":
        InventoryOverviewAdapter(manager).update_drop(drop)
    elif event == "campaign":
        adapter = InventoryOverviewAdapter(manager)
        adapter._campaign_active[campaign.id] = False

        async def run():
            await adapter.add_campaign(campaign)
            await notifier.flush_notifications()

        asyncio.run(run())
    else:
        monkeypatch.setenv("WEBUI_TWITCH_LOGIN", "device-code")
        LoginFormAdapter(manager).update(_("gui", "login", "logged_in"), 42)

    assert len(delivery.messages) == 1
    body = delivery.messages[0][1]
    assert body.count("Channel:") == 1
    assert body.endswith("Channel: TwitchRivals (https://www.twitch.tv/twitchrivals)")
