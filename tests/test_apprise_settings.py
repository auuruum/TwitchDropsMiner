from types import SimpleNamespace
from unittest.mock import MagicMock

from webui.components.settings.general_section import GeneralSection


def test_apprise_urls_are_saved_one_per_line():
    settings = SimpleNamespace(apprise_urls=[])
    settings.save = MagicMock()
    section = object.__new__(GeneralSection)
    section._manager = SimpleNamespace(_twitch=SimpleNamespace(settings=settings))

    section._on_apprise_urls_change(" tgram://bot/chat \n\n discord://webhook ")

    assert settings.apprise_urls == ["tgram://bot/chat", "discord://webhook"]
    settings.save.assert_called_once_with(force=True)
