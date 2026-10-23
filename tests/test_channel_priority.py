"""
Channel ranking: within one game, channels that progress more exclusive campaigns
(streamer-specific drops) win over wide-ACL campaigns (Twitch Rivals),
which win over campaigns without an ACL (general drops).
"""

import importlib.util
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from constants import MAX_INT
from tests.conftest import PROJECT_ROOT


def _import_twitch():
    if "twitch" in sys.modules:
        return sys.modules["twitch"].Twitch
    if "gui" not in sys.modules:
        # same stub main_webui.py injects, so twitch.py imports without tkinter
        from webui import WebUIManager

        gui_stub = types.ModuleType("gui")
        gui_stub.GUIManager = WebUIManager
        sys.modules["gui"] = gui_stub
    # conftest stubs the "websocket" name for engineio - load the project file only while
    # twitch.py imports it, then put the stub back
    spec = importlib.util.spec_from_file_location("websocket", PROJECT_ROOT / "websocket.py")
    project_ws = importlib.util.module_from_spec(spec)
    stub = sys.modules.get("websocket")
    sys.modules["websocket"] = project_ws
    try:
        spec.loader.exec_module(project_ws)
        import twitch
    finally:
        if stub is not None:
            sys.modules["websocket"] = stub
    return twitch.Twitch


Twitch = _import_twitch()

RUST = SimpleNamespace(name="Rust")
OTHER = SimpleNamespace(name="Other")


def make_channel(name, *, game=RUST, acl_based=False):
    return SimpleNamespace(name=name, game=game, acl_based=acl_based, online=game is not None)


def make_campaign(allowed, *, game=RUST, earnable=True):
    def can_earn(channel=None, ignore_channel_status=False):
        return earnable and (not allowed or channel in allowed)

    return SimpleNamespace(game=game, allowed_channels=allowed, can_earn=can_earn)


@pytest.fixture
def channels():
    streamer = make_channel("streamer", acl_based=True)
    rivals = [make_channel(f"rivals{i}", acl_based=True) for i in range(20)]
    general = make_channel("general")
    return SimpleNamespace(streamer=streamer, rivals=rivals[0], all_rivals=rivals, general=general)


def make_twitch(campaigns, *, watching=None, wanted=(RUST,)):
    twitch = object.__new__(Twitch)
    twitch.inventory = list(campaigns)
    twitch.wanted_games = list(wanted)
    twitch.watching_channel = MagicMock()
    twitch.watching_channel.get_with_default.return_value = watching
    twitch.can_watch = lambda channel: channel.online
    return twitch


def rust_campaigns(channels, *, streamer_earnable=True):
    return [
        make_campaign([channels.streamer], earnable=streamer_earnable),
        make_campaign(channels.all_rivals),
        make_campaign([]),
    ]


def test_exclusivity_orders_streamer_rivals_general(channels):
    twitch = make_twitch(rust_campaigns(channels))
    assert twitch.get_exclusivity(channels.streamer) == 1
    assert twitch.get_exclusivity(channels.rivals) == len(channels.all_rivals)
    assert twitch.get_exclusivity(channels.general) == MAX_INT
    ranked = sorted([channels.general, channels.rivals, channels.streamer], key=twitch._switch_order)
    assert ranked == [channels.streamer, channels.rivals, channels.general]


def test_finished_streamer_drop_gets_no_bonus(channels):
    twitch = make_twitch(rust_campaigns(channels, streamer_earnable=False))
    assert twitch.get_exclusivity(channels.streamer) == MAX_INT
    assert twitch._switch_order(channels.rivals) < twitch._switch_order(channels.streamer)


def test_campaigns_for_other_games_are_ignored(channels):
    twitch = make_twitch([make_campaign([channels.streamer], game=OTHER)])
    assert twitch.get_exclusivity(channels.streamer) == MAX_INT


def test_offline_acl_channel_keeps_exclusivity(channels):
    offline = make_channel("offline", game=None, acl_based=True)
    twitch = make_twitch([make_campaign([offline])])
    assert twitch.get_exclusivity(offline) == 1


def test_game_priority_still_comes_first(channels):
    other_streamer = make_channel("other", game=OTHER, acl_based=True)
    twitch = make_twitch(
        [make_campaign([channels.streamer]), make_campaign([other_streamer], game=OTHER)],
        wanted=(OTHER, RUST),
    )
    assert twitch._switch_order(other_streamer) < twitch._switch_order(channels.streamer)
    channels.streamer.game = OTHER  # streamer plays a different game - Rust drops don't apply
    assert twitch.get_exclusivity(channels.streamer) == MAX_INT


def test_switches_from_rivals_to_streamer_going_online(channels):
    twitch = make_twitch(rust_campaigns(channels), watching=channels.rivals)
    assert twitch.should_switch(channels.streamer)


def test_switches_from_general_to_rivals(channels):
    twitch = make_twitch(rust_campaigns(channels), watching=channels.general)
    assert twitch.should_switch(channels.rivals)


def test_does_not_switch_away_from_streamer(channels):
    twitch = make_twitch(rust_campaigns(channels), watching=channels.streamer)
    assert not twitch.should_switch(channels.rivals)
    assert not twitch.should_switch(channels.general)


def test_equal_rivals_channels_do_not_switch(channels):
    twitch = make_twitch(rust_campaigns(channels), watching=channels.rivals)
    assert not twitch.should_switch(channels.all_rivals[1])
