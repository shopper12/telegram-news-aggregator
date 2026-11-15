import asyncio
from types import SimpleNamespace

import pytest

from telegram_news.settings import ChannelConfig
from telegram_news.telegram_client import _resolve_entity


class FakeClient:
    def __init__(self, dialogs):
        self.dialogs = dialogs
        self.get_entity_calls = []

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog

    async def get_entity(self, value):
        self.get_entity_calls.append(value)
        return SimpleNamespace(username=value)


def test_private_tme_c_url_resolves_from_existing_dialog_membership():
    entity = SimpleNamespace(id=1579665908, title="해외주식 루틴")
    client = FakeClient([SimpleNamespace(entity=entity, id=-1001579665908)])
    channel = ChannelConfig(name="해외주식 루틴", invite_link="https://t.me/c/1579665908")
    assert asyncio.run(_resolve_entity(client, channel)) is entity
    assert client.get_entity_calls == []


def test_private_tme_c_post_link_resolves_from_dialog():
    entity = SimpleNamespace(id=1579665908)
    client = FakeClient([SimpleNamespace(entity=entity, id=-1001579665908)])
    channel = ChannelConfig(name="해외주식 루틴", invite_link="https://t.me/c/1579665908/101")
    assert asyncio.run(_resolve_entity(client, channel)) is entity


def test_private_tme_c_unjoined_returns_actionable_error():
    client = FakeClient([SimpleNamespace(entity=SimpleNamespace(id=123), id=-100123)])
    channel = ChannelConfig(name="해외주식 루틴", invite_link="https://t.me/c/1579665908")
    with pytest.raises(ValueError, match="SAME account as TELEGRAM_STRING_SESSION"):
        asyncio.run(_resolve_entity(client, channel))


def test_public_channel_uses_existing_username_resolution():
    client = FakeClient([])
    channel = ChannelConfig(name="공개 채널", username="news_channel")
    entity = asyncio.run(_resolve_entity(client, channel))
    assert entity.username == "news_channel"
    assert client.get_entity_calls == ["news_channel"]
