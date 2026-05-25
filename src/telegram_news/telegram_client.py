from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.sessions import StringSession

from .settings import Settings, ChannelConfig
from .store import NewsMessage
from .normalizer import normalize_text


AD_INVITE_PATTERNS = [
    "t.me/joinchat",
    "t.me/+",
    "무료방",
    "리딩방",
    "유료방",
    "추천방",
    "선착순",
    "수익인증",
    "구독하기",
    "입장하기",
]

INFORMATIVE_SIGNALS = [
    "수주", "계약", "실적", "공시", "발표", "승인", "허가",
    "금리", "환율", "억", "조", "매출", "영업이익",
    "코스피", "코스닥", "나스닥", "연준", "한은",
    "상장", "합병", "인수", "배당", "증자",
    "n.news.naver.com", "news.naver.com", "hankyung.com",
    "mk.co.kr", "yna.co.kr", "reuters.com", "bloomberg.com",
]

SOURCE_TELEGRAM_LINK_RE = re.compile(r"https?://t\.me/(?:c/\d+/\d+|[A-Za-z0-9_]{4,}/\d+)\b", re.IGNORECASE)


def _is_obvious_junk(text: str) -> bool:
    """명백한 광고/잡담만 차단한다.

    불확실하면 통과시킨다. 뉴스 누락이 잡담 통과보다 손해가 크기 때문이다.
    t.me/+ 초대 링크와 t.me/채널/글번호 원문 링크는 다르게 취급한다.
    """
    raw = text.strip()
    lower = raw.lower()

    if any(sig in lower for sig in INFORMATIVE_SIGNALS):
        return False

    # 다른 채널의 원문 링크는 정보성 신호로 본다. 초대 링크(t.me/+)는 여기에 해당하지 않는다.
    if SOURCE_TELEGRAM_LINK_RE.search(lower):
        return False

    if any(pattern in lower for pattern in AD_INVITE_PATTERNS):
        return True

    compact = re.sub(r"[\s\W_]+", "", lower, flags=re.UNICODE)
    greeting_words = ["좋은하루", "화이팅", "감사합니다", "수고", "굿모닝", "좋은아침"]
    if len(raw) < 30 and any(word in compact for word in greeting_words):
        return True

    if len(raw) < 30 and not re.search(r"[0-9A-Za-z가-힣]{3,}", raw):
        return True

    if len(raw) < 18:
        return True

    return False


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
                    if _is_obvious_junk(text):
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
