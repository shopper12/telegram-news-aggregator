from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
import re

import requests
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
STOCK_CATEGORIES = {"stock", "korea_stock", "us_stock", "kr_stock"}
IMAGE_MAX_BYTES = int(os.getenv("IMAGE_OCR_MAX_BYTES", "4000000"))


def _is_obvious_junk(text: str) -> bool:
    """명백한 광고/잡담만 차단한다.

    불확실하면 통과시킨다. 뉴스 누락이 잡담 통과보다 손해가 크기 때문이다.
    t.me/+ 초대 링크와 t.me/채널/글번호 원문 링크는 다르게 취급한다.
    """
    raw = text.strip()
    lower = raw.lower()

    if any(sig in lower for sig in INFORMATIVE_SIGNALS):
        return False

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
    internal_id = str(entity_id)
    if internal_id.startswith("-100"):
        internal_id = internal_id[4:]
    return f"https://t.me/c/{internal_id}/{msg_id}"


def _media_hint(msg) -> str | None:
    if getattr(msg, "photo", None) is not None:
        return "[첨부이미지]"
    document = getattr(msg, "document", None)
    mime = str(getattr(document, "mime_type", "") or "") if document is not None else ""
    if mime.startswith("image/"):
        return "[첨부이미지]"
    if getattr(msg, "media", None) is not None:
        return "[첨부미디어]"
    return None


def _image_mime_type(msg) -> str | None:
    if getattr(msg, "photo", None) is not None:
        return "image/jpeg"
    document = getattr(msg, "document", None)
    mime = str(getattr(document, "mime_type", "") or "") if document is not None else ""
    return mime if mime.startswith("image/") else None


def _extract_json_object(text: str) -> dict | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start:end + 1])
                return data if isinstance(data, dict) else None
            except Exception:
                return None
    return None


def _format_list(values: object, limit: int = 6) -> str:
    if not isinstance(values, list):
        return ""
    cleaned = [str(v).strip() for v in values if str(v).strip()]
    return ", ".join(cleaned[:limit])


def _gemini_extract_stock_image_text(image_bytes: bytes, mime_type: str, settings: Settings) -> str | None:
    if not settings.gemini_api_key:
        return None
    if not image_bytes or len(image_bytes) > IMAGE_MAX_BYTES:
        return None

    model = settings.gemini_model or "gemini-2.5-flash"
    prompt = (
        "이 이미지는 텔레그램 주식/시장 뉴스 캡처일 수 있다. 이미지 안의 텍스트를 직접 읽고 판단한다.\n"
        "종목명, 기업명, 티커, 한국 6자리 종목코드가 보이는 경우에만 relevant=true로 한다.\n"
        "거시경제 문구만 있고 종목명/티커/종목코드가 없으면 relevant=false로 한다.\n"
        "광고, 리딩방, 수익인증, 단순 차트 인증, 이모지만 있는 이미지는 relevant=false로 한다.\n"
        "종목명이 불확실하면 추정하지 말고 제외한다.\n"
        "반드시 JSON 객체만 반환한다. 형식:\n"
        "{\"relevant\":true/false,\"headline\":\"이미지 핵심 제목\",\"companies\":[\"종목명\"],\"tickers\":[\"티커 또는 6자리 코드\"],\"summary\":\"왜 시장에 중요한지 1문장\"}"
    )
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 600,
            "responseMimeType": "application/json",
        },
    }
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"},
            json=body,
            timeout=25,
        )
        response.raise_for_status()
        data = response.json()
        raw = "".join(
            part.get("text", "")
            for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        ).strip()
        parsed = _extract_json_object(raw)
        if not parsed or parsed.get("relevant") is not True:
            return None
        companies = _format_list(parsed.get("companies"))
        tickers = _format_list(parsed.get("tickers"))
        if not companies and not tickers:
            return None
        headline = str(parsed.get("headline") or "종목명 포함 이미지 뉴스").strip()
        summary = str(parsed.get("summary") or "이미지 안에 종목명이 포함되어 원문 확인 필요").strip()
        parts = [f"[이미지OCR] {headline}"]
        if companies:
            parts.append(f"이미지종목: {companies}")
        if tickers:
            parts.append(f"이미지티커: {tickers}")
        parts.append(f"요약: {summary}")
        parts.append("[첨부이미지]")
        return "\n".join(parts)
    except Exception:
        return None


async def _extract_stock_image_news(client: TelegramClient, msg, settings: Settings, media_hint: str | None, channel: ChannelConfig) -> str | None:
    if media_hint != "[첨부이미지]":
        return None
    if channel.category.lower() not in STOCK_CATEGORIES:
        return None
    mime_type = _image_mime_type(msg)
    if not mime_type:
        return None
    try:
        image_bytes = await client.download_media(msg, file=bytes)
    except Exception:
        return None
    if not isinstance(image_bytes, (bytes, bytearray)):
        return None
    return _gemini_extract_stock_image_text(bytes(image_bytes), mime_type, settings)


def _build_collect_text(raw_text: str, image_news_text: str | None) -> str | None:
    raw_text = raw_text.strip()
    if image_news_text:
        return f"{raw_text}\n{image_news_text}".strip() if raw_text else image_news_text
    if raw_text:
        return raw_text
    return None


def _image_ocr_limit() -> int:
    try:
        return max(0, int(os.getenv("IMAGE_OCR_MAX_PER_RUN", "20")))
    except Exception:
        return 20


async def collect_messages(
    settings: Settings,
    channels: list[ChannelConfig],
    hours: int = 6,
    limit_per_channel: int = 200,
) -> list[NewsMessage]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    messages: list[NewsMessage] = []
    image_ocr_attempts = 0
    max_image_ocr = _image_ocr_limit()

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
                    msg_date = msg.date
                    if msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=timezone.utc)

                    if msg_date < since:
                        break

                    raw_text = (msg.message or "").strip()
                    media_hint = _media_hint(msg)
                    image_news_text = None
                    if media_hint == "[첨부이미지]" and image_ocr_attempts < max_image_ocr:
                        image_ocr_attempts += 1
                        image_news_text = await _extract_stock_image_news(client, msg, settings, media_hint, ch)

                    text = _build_collect_text(raw_text, image_news_text)
                    if not text:
                        continue

                    normalized = normalize_text(text)
                    if len(normalized) < 10:
                        continue
                    if _is_obvious_junk(text) and image_news_text is None:
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
