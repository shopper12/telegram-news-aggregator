from __future__ import annotations

from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.errors import RPCError

from .settings import Settings, ChannelConfig
from .store import NewsMessage
from .normalizer import normalize_text


async def _resolve_entity(client: TelegramClient, channel: ChannelConfig):
    if channel.username:
        return await client.get_entity(channel.username)
    if channel.invite_link:
        return await client.get_entity(channel.invite_link)
    raise ValueError(f"Channel has neither username nor invite_link: {channel.name}")


async def collect_messages(
    settings: Settings,
    channels: list[ChannelConfig],
    hours: int = 6,
    limit_per_channel: int = 200,
) -> list[NewsMessage]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    messages: list[NewsMessage] = []

    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

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
                        )
                    )
            except RPCError as e:
                print(f"[WARN] Telegram RPC error for {ch.name}: {e}")
            except Exception as e:
                print(f"[WARN] Failed to collect {ch.name}: {e}")
    finally:
        await client.disconnect()

    return messages
