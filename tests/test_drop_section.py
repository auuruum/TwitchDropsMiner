from types import SimpleNamespace

from webui.components.main.drop_section import DropSection


def test_tick_refreshes_progress_values():
    campaign = SimpleNamespace(
        game=SimpleNamespace(name="Game"),
        name="Campaign",
        progress=0.25,
        claimed_drops=1,
        total_drops=4,
        remaining_minutes=180,
    )
    drop = SimpleNamespace(
        campaign=campaign,
        progress=0.5,
        remaining_minutes=30,
        rewards_text=lambda: "Reward",
    )
    section = object.__new__(DropSection)
    section._current_drop = drop
    section._countdown_active = False
    section._countdown_start_time = None
    section._progress_seconds = 60

    section.tick()

    assert section._campaign_progress_value == 0.25
    assert section._drop_progress_value == 0.5
