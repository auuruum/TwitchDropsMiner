from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from constants import State
from webui import farm_status
from webui.handlers import WebUIOutputHandler


@pytest.fixture
def farm(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(farm_status, "monotonic", lambda: clock[0])
    notify = MagicMock()
    error = MagicMock()
    monkeypatch.setattr(farm_status, "send_apprise", notify)
    monkeypatch.setattr(farm_status, "send_miner_error", error)
    channel = SimpleNamespace(name="streamer", game=SimpleNamespace(name="Game"), online=True)
    campaign = SimpleNamespace(name="Campaign", game=channel.game)
    drop = SimpleNamespace(
        id="drop", real_current_minutes=10, required_minutes=100,
        extra_current_minutes=0, is_claimed=False, campaign=campaign,
        rewards_text=lambda: "Reward",
    )
    campaign.first_drop = drop
    twitch = SimpleNamespace(
        settings=SimpleNamespace(apprise_urls=[]),
        gui=SimpleNamespace(running=True, close_requested=False),
        _auth_state=SimpleNamespace(_logged_in=SimpleNamespace(is_set=lambda: True)),
        _state=State.CHANNEL_SWITCH,
        watching_channel=MagicMock(),
        get_active_campaign=MagicMock(return_value=campaign),
    )
    twitch.watching_channel.get_with_default.return_value = channel
    return SimpleNamespace(
        monitor=farm_status.FarmStatusNotifier(twitch), twitch=twitch,
        channel=channel, drop=drop, campaign=campaign, clock=clock,
        notify=notify, error=error,
    )


def titles(farm):
    return [call.args[1] for call in farm.notify.call_args_list]


def test_stream_selection_reports_start_without_claiming_progress(farm):
    farm.monitor.check()
    farm.clock[0] = 5
    farm.monitor.check()
    assert titles(farm) == ["Farm Started"]
    assert "streamer" in farm.notify.call_args.args[2]
    assert "Waiting for Twitch" in farm.notify.call_args.args[2]


def test_first_actual_minute_is_reported_before_next_five_percent(farm):
    farm.monitor.check()
    farm.drop.real_current_minutes += 1
    farm.clock[0] = 60
    farm.monitor.check()
    farm.monitor.check()
    assert titles(farm) == ["Farm Started", "Farm Confirmed"]
    assert "11/100 min" in farm.notify.call_args.args[2]


def test_local_estimates_do_not_mask_stall_or_confirm_startup(farm):
    farm.monitor.check()
    farm.drop.extra_current_minutes = 10
    farm.clock[0] = 600
    farm.monitor.check()
    farm.clock[0] = 1200
    farm.monitor.check()
    assert titles(farm) == ["Farm Started", "Miner Warning"]
    assert "git pull" in farm.notify.call_args.args[2]


def test_inventory_refresh_and_channel_switch_do_not_reset_stall_clock(farm):
    farm.monitor.check()
    farm.clock[0] = 599
    farm.campaign.first_drop = SimpleNamespace(**vars(farm.drop))
    farm.channel.name = "another-streamer"
    farm.monitor.check()
    farm.clock[0] = 600
    farm.monitor.check()
    assert titles(farm) == ["Farm Started", "Miner Warning"]


def test_real_progress_restarts_timeout_and_reports_recovery_once(farm):
    farm.monitor.check()
    farm.clock[0] = 600
    farm.monitor.check()
    farm.drop.real_current_minutes += 1
    farm.clock[0] = 605
    farm.monitor.check()
    farm.drop.real_current_minutes += 1
    farm.clock[0] = 660
    farm.monitor.check()
    farm.clock[0] = 1259
    farm.monitor.check()
    assert titles(farm) == ["Farm Started", "Miner Warning", "Farm Recovered"]
    farm.clock[0] = 1260
    farm.monitor.check()
    assert titles(farm)[-1] == "Miner Warning"


@pytest.mark.parametrize("inactive", ["idle", "offline", "logout", "shutdown"])
def test_no_stall_alarm_when_mining_is_not_expected(farm, inactive):
    farm.monitor.check()
    if inactive == "idle":
        farm.twitch._state = State.IDLE
        farm.twitch.watching_channel.get_with_default.return_value = None
    elif inactive == "offline":
        farm.channel.online = False
    elif inactive == "logout":
        farm.twitch._auth_state._logged_in.is_set = lambda: False
    else:
        farm.twitch.gui.close_requested = True
    farm.clock[0] = 3600
    farm.monitor.check()
    farm.monitor.check()
    assert "Miner Warning" not in titles(farm)
    assert titles(farm).count("Farm Ready") == (inactive == "idle")


@pytest.mark.parametrize("completed", ["claimed", "full"])
def test_completed_drop_does_not_trigger_stall(farm, completed):
    farm.monitor.check()
    if completed == "claimed":
        farm.drop.is_claimed = True
    else:
        farm.drop.real_current_minutes = farm.drop.required_minutes
    farm.clock[0] = 1200
    farm.monitor.check()
    assert "Miner Warning" not in titles(farm)


def test_lost_drop_status_still_produces_a_watch_warning(farm):
    farm.monitor.check()
    farm.twitch.get_active_campaign.return_value = None
    farm.clock[0] = 600
    farm.monitor.check()
    assert titles(farm)[-1] == "Miner Warning"


def test_background_failure_is_reported_without_exception_secrets(farm):
    import logging
    import sys

    output = SimpleNamespace(print=MagicMock(), notifications=farm.monitor)
    try:
        raise ValueError("sensitive-token")
    except ValueError:
        record = logging.LogRecord(
            "TwitchDrops", logging.ERROR, "", 0,
            "Exception in _watch_loop task", (), sys.exc_info(),
        )
    handler = WebUIOutputHandler(output)
    handler.emit(record)
    farm.twitch._state = State.EXIT
    farm.monitor.check()
    farm.error.assert_called_once()
    assert "ValueError" in farm.error.call_args.args[1]
    assert "sensitive-token" not in farm.error.call_args.args[1]


def test_normal_shutdown_does_not_report_backend_error(farm):
    farm.twitch.gui.close_requested = True
    farm.twitch._state = State.EXIT
    farm.monitor.check()
    farm.error.assert_not_called()


def test_existing_progress_notifications_still_use_five_percent_buckets(monkeypatch):
    from webui.components.main import drop_section

    notify = MagicMock()
    monkeypatch.setattr(drop_section, "send_apprise", notify)
    section = object.__new__(drop_section.DropSection)
    section._manager = SimpleNamespace(_twitch=SimpleNamespace(settings=SimpleNamespace()))
    section._notified_progress = {}
    drop = SimpleNamespace(
        id="drop", is_claimed=False, required_minutes=100, current_minutes=4,
        progress=0.04, rewards_text=lambda: "Reward",
        campaign=SimpleNamespace(game=SimpleNamespace(name="Game"), name="Campaign"),
    )
    for minutes in (4, 5, 6, 9, 10):
        drop.current_minutes = minutes
        drop.progress = minutes / 100
        section._notify_progress(drop)
    assert notify.call_count == 2
    assert all(call.args[1] == "\U0001f4c8 Drop Progress" for call in notify.call_args_list)


def test_logout_time_does_not_count_toward_next_session_stall(farm):
    farm.monitor.check()
    farm.twitch._auth_state._logged_in.is_set = lambda: False
    farm.clock[0] = 3600
    farm.monitor.check()
    farm.twitch._auth_state._logged_in.is_set = lambda: True
    farm.monitor.check()
    assert "Miner Warning" not in titles(farm)


def test_first_confirmed_increase_can_complete_the_drop(farm):
    farm.monitor.check()
    farm.drop.real_current_minutes = farm.drop.required_minutes
    farm.monitor.check()
    assert "Farm Confirmed" in titles(farm)


def test_login_prompt_notifies_once_until_successful_login(monkeypatch):
    from translate import _
    from webui.adapters import login_form
    monkeypatch.setenv("WEBUI_TWITCH_LOGIN", "device-code")
    notify = MagicMock()
    monkeypatch.setattr(login_form, "send_apprise", notify)
    adapter = login_form.LoginFormAdapter(MagicMock())
    required = _("gui", "login", "required")
    adapter.update(required, None)
    adapter.update(required, None)
    adapter.update(_("gui", "login", "logged_in"), 42)
    adapter.update(required, None)
    assert [call.args[1] for call in notify.call_args_list] == [
        "Twitch Login Required", "\u26cf Farm Online", "Twitch Login Required",
    ]


def test_error_notification_rearms_after_confirmed_recovery(farm):
    farm.monitor.check()
    farm.monitor.error("First failure")
    farm.drop.real_current_minutes += 1
    farm.monitor.check()
    assert titles(farm)[-1] == "Farm Recovered"
    farm.monitor.error("Later failure")
    assert farm.error.call_count == 2
