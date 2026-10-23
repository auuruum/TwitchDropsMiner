"""
Drops-enabled check for ACL channels: Twitch's AvailableDrops response can omit
game campaigns, so ACL membership of an earnable campaign has to count on its own.
"""

from types import SimpleNamespace

from channel import Channel


def make_campaign(allowed, *, earnable=True):
    def can_earn(channel=None, ignore_channel_status=False):
        return earnable and (not allowed or channel in allowed)

    return SimpleNamespace(id=f"c{id(allowed)}", allowed_channels=allowed, can_earn=can_earn)


def make_channel(campaigns, channel_id=1):
    twitch = SimpleNamespace(inventory=campaigns, _campaigns={c.id: c for c in campaigns})
    channel = object.__new__(Channel)
    channel._twitch = twitch
    channel.id = channel_id
    return channel


def check(channel, available_drops=()):
    return Channel._check_drops_enabled(channel, list(available_drops))


def test_acl_channel_counts_without_available_drops():
    campaigns = []
    channel = make_channel(campaigns)
    campaigns.append(make_campaign([channel]))
    assert check(channel)


def test_acl_channel_with_finished_campaign_does_not_count():
    campaigns = []
    channel = make_channel(campaigns)
    campaigns.append(make_campaign([channel], earnable=False))
    assert not check(channel)


def test_campaign_without_acl_needs_available_drops():
    general = make_campaign([])
    channel = make_channel([general])
    assert not check(channel)
    assert check(channel, [{"id": general.id}])


def test_unrelated_acl_does_not_count():
    other = make_channel([], channel_id=2)
    channel = make_channel([make_campaign([other])])
    assert not check(channel)
