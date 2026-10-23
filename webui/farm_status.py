from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

from constants import State
from webui.apprise_notifier import RECOVERY_HINT, send_apprise, send_miner_error

if TYPE_CHECKING:
    from twitch import Twitch


class FarmStatusNotifier:
    STALL_TIMEOUT = 10 * 60

    def __init__(self, twitch: "Twitch") -> None:
        self._twitch = twitch
        self._started = False
        self._confirmed = False
        self._idle_notified = False
        self._stall_notified = False
        self._error_notified = False
        self._drop_id: str | None = None
        self._minutes = 0
        self._last_progress_at: float | None = None

    def error(self, message: str) -> None:
        if not self._error_notified:
            self._error_notified = True
            send_miner_error(
                self._twitch.settings, message,
                channel=self._twitch.watching_channel.get_with_default(None),
            )

    def check(self) -> None:
        twitch = self._twitch
        if not twitch.gui.running or twitch.gui.close_requested:
            return
        if twitch._state is State.EXIT:
            self.error("The mining backend stopped unexpectedly.")
            return
        if not twitch._auth_state._logged_in.is_set():
            self._drop_id = None
            self._last_progress_at = None
            return
        channel = twitch.watching_channel.get_with_default(None)
        if channel is None or not channel.online:
            self._drop_id = None
            self._last_progress_at = None
            if twitch._state is State.IDLE and not self._idle_notified:
                self._idle_notified = True
                send_apprise(
                    twitch.settings,
                    "Farm Ready",
                    "Signed in and running. No eligible drops or live channels are available "
                    "right now. The miner is waiting and will start automatically.",
                    channel=channel,
                )
            return

        now = monotonic()
        if not self._started:
            self._started = True
            game = channel.game.name if channel.game is not None else "Unknown"
            send_apprise(
                twitch.settings,
                "Farm Started",
                f"Game: {game}\n"
                "Stream selected; watching has started. Waiting for Twitch to confirm "
                "the first progress increase.",
                channel=channel,
            )

        campaign = twitch.get_active_campaign(channel)
        drop = campaign.first_drop if campaign is not None else None
        details = ""
        if drop is not None:
            minutes = drop.real_current_minutes
            if drop.is_claimed or drop.required_minutes <= 0:
                self._last_progress_at = None
                return
            details = (
                f"Campaign: {campaign.game.name} | {campaign.name}"
                f"\nDrop: {drop.rewards_text()}"
                f"\nTwitch progress: {minutes}/{drop.required_minutes} min"
            )
            if drop.id != self._drop_id:
                self._drop_id = drop.id
                self._minutes = minutes
                self._last_progress_at = now
            else:
                # current_minutes also includes optimistic local estimates.
                if minutes > self._minutes:
                    self._last_progress_at = now
                    recovering = self._stall_notified or self._error_notified
                    if recovering or not self._confirmed:
                        send_apprise(
                            twitch.settings,
                            "Farm Recovered" if recovering else "Farm Confirmed",
                            "Twitch confirmed that drop progress is increasing.\n" + details,
                            channel=channel,
                        )
                    self._confirmed = True
                    self._stall_notified = False
                    self._error_notified = False
                self._minutes = minutes
            if minutes >= drop.required_minutes:
                self._last_progress_at = None
                return

        if self._last_progress_at is None:
            self._last_progress_at = now
        if now - self._last_progress_at >= self.STALL_TIMEOUT and not self._stall_notified:
            self._stall_notified = True
            send_apprise(
                twitch.settings,
                "Miner Warning",
                "No Twitch-confirmed progress increase for 10 minutes while watching. "
                "Mining may be stalled.\n" + details + "\n\n" + RECOVERY_HINT,
                channel=channel,
            )
