from __future__ import annotations

from typing import TYPE_CHECKING

from webui.apprise_notifier import send_apprise

if TYPE_CHECKING:
    from webui.manager import WebUIManager


class InventoryOverviewAdapter:
    """
    Mirrors InventoryOverview - triggers inventory panel refreshes
    when the backend calls clear/add_campaign/update_drop.
    Campaign data is read directly from twitch.inventory.
    """

    def __init__(self, manager: "WebUIManager"):
        self._manager = manager
        self._campaign_active: dict[str, bool] = {}
        self._notified_claims: set[str] = set()

    def clear(self):
        self._manager.inventory_panel.clear()

    async def add_campaign(self, campaign) -> None:
        self._manager.inventory_panel.add_campaign(campaign)
        was_active = self._campaign_active.get(campaign.id)
        self._campaign_active[campaign.id] = campaign.active
        if campaign.active and was_active is False:
            send_apprise(
                self._manager._twitch.settings,
                "🚀 Campaign Started",
                f"\n🎮 Campaign: {campaign.game.name} | {campaign.name}",
            )

    def update_drop(self, drop) -> None:
        self._manager.inventory_panel.update_drop(drop)
        if drop.is_claimed and drop.id not in self._notified_claims:
            self._notified_claims.add(drop.id)
            campaign = drop.campaign
            send_apprise(
                self._manager._twitch.settings,
                "✅ Drop Claimed",
                "\n" + "\n".join(
                    (
                        "🎁 Reward:",
                        f"{campaign.game.name} | {campaign.name} ({campaign.claimed_drops}/{campaign.total_drops}) | {drop.rewards_text()}",
                    )
                ),
            )

    def configure_theme(self, *, bg: str):
        pass
