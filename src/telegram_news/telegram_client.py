from __future__ import annotations

from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.sessions import StringSession

from .settings import Settings, ChannelConfig
from .store import NewsMessage
from .normalizer import normalize_text


async def _resolve_entity(client: TelegramClient, channel: ChannelConfig):
    if channel.username:
        return await client.get_entity(channel.username)
    if channel.invite_link:
        return await client.get_entity(channel.invite_link)
    raise ValueError(f"Channel has neither username nor invite_link: {channel.name}")


def _make_client(settings: Settings) -> TelegramClient:
    if settings.telegram_string_session:
        session = StringSession(settings.telegram_string_session)
    else:
        session = settings.telegram_session_name

    return TelegramClient(
        session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )


def _source_url(entity, msg_id: int) -> str | None:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"

    entity_id = getattr(entity, "id", None)
    if entity_id is None:
        return None
    # 비공개 채널/그룹은 t.me/c/<internal_id>/<message_id> 형식.
    internal_id = str(entity_id)
    if internal_id.startswith("-100"):
        internal_id = internal_id[4:]
    return f"https://t.me/c/{internal_id}/{msg_id}"


async def collect_messages(
    settings: Settings,
    channels: list[ChannelConfig],
    hours: int = 6,
    limit_per_channel: int = 200,
) -> list[NewsMessage]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    messages: list[NewsMessage] = []

    client = _make_client(settings)

    await client.connect()
    try:
        if not await client.is_user_authorized():
            if not settings.telegram_phone:
                raise RuntimeError(
                    "Telegram login is not authorized yet. "
                    "Set TELEGRAM_PHONE=+8210xxxxxxxx in .env and run again. "
                    "Do not enter a bot token here."
                )
            await client.start(phone=settings.telegram_phone)

        for ch in channels:
            try:
                entity = await _resolve_entity(client, ch)
                async for msg in client.iter_messages(entity, limit=limit_per_channel):
                    if not msg.message:
                        continue

                    msg_date = msg.date
                    if msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=timezone.utc)

                    if msg_date < since:
                        break

                    text = msg.message.strip()
                    normalized = normalize_text(text)
                    if len(normalized) < 10:
                        continue

                    messages.append(
                        NewsMessage(
                            channel_name=ch.name,
                            channel_username=ch.source_key,
                            category=ch.category,
                            message_id=msg.id,
                            message_date=msg_date,
                            text=text,
                            normalized_text=normalized,
                            message_url=_source_url(entity, msg.id),
                        )
                    )
            except RPCError as e:
                print(f"[WARN] Telegram RPC error for {ch.name}: {e}")
            except Exception as e:
                print(f"[WARN] Failed to collect {ch.name}: {e}")
    finally:
        await client.disconnect()

    return messages
