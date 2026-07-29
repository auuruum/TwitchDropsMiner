from unittest.mock import MagicMock

from webui.adapters.inventory_overview import InventoryOverviewAdapter


def test_clear_also_resets_the_current_drop():
    manager = MagicMock()

    InventoryOverviewAdapter(manager).clear()

    manager.clear_drop.assert_called_once_with()
    manager.inventory_panel.clear.assert_called_once_with()
