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
        self._notified_campaigns: set[str] = set()
        self._notified_claims: set[str] = set()

    def clear(self):
        self._manager.inventory_panel.clear()

    async def add_campaign(self, campaign) -> None:
        self._manager.inventory_panel.add_campaign(campaign)
        if campaign.active and campaign.id not in self._notified_campaigns:
            self._notified_campaigns.add(campaign.id)
            send_apprise(
                self._manager._twitch.settings,
                "🚀 Campaign Started",
                f"🎮 Campaign: {campaign.game.name} | {campaign.name}",
            )

    def update_drop(self, drop) -> None:
        self._manager.inventory_panel.update_drop(drop)
        if drop.is_claimed and drop.id not in self._notified_claims:
            self._notified_claims.add(drop.id)
            campaign = drop.campaign
            send_apprise(
                self._manager._twitch.settings,
                "✅ Drop Claimed",
                "\n".join(
                    (
                        "🎁 Reward:",
                        f"{campaign.game.name} | {campaign.name} ({campaign.claimed_drops}/{campaign.total_drops}) | {drop.rewards_text()}",
                    )
                ),
            )

    def configure_theme(self, *, bg: str):
        pass
