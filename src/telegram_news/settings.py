from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
from dotenv import load_dotenv
import yaml


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    username: str | None = None
    invite_link: str | None = None
    category: str = "general"

    @property
    def source_key(self) -> str:
        if self.username:
            return self.username
        if self.invite_link:
            digest = hashlib.sha256(self.invite_link.encode("utf-8")).hexdigest()[:16]
            return f"invite:{digest}"
        return self.name


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_name: str
    telegram_phone: str | None
    telegram_string_session: str | None
    database_path: Path
    channel_config_path: Path
    timezone: str
    openai_api_key: str | None
    openai_model: str
    telegram_bot_token: str | None
    telegram_target_chat_id: str | None
    telegram_target_chat_ids: list[str]


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Only sqlite:/// DATABASE_URL is supported in this starter project.")
    return Path(database_url[len(prefix):])


def _split_chat_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


def load_settings() -> Settings:
    load_dotenv()

    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    if not api_id_raw or not api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH are required. "
            "Create .env from .env.example first."
        )

    single_chat_id = os.getenv("TELEGRAM_TARGET_CHAT_ID") or None
    multi_chat_ids = _split_chat_ids(os.getenv("TELEGRAM_TARGET_CHAT_IDS"))
    if single_chat_id and single_chat_id not in multi_chat_ids:
        multi_chat_ids.insert(0, single_chat_id)

    return Settings(
        telegram_api_id=int(api_id_raw),
        telegram_api_hash=api_hash,
        telegram_session_name=os.getenv("TELEGRAM_SESSION_NAME", "telegram_news_session").strip(),
        telegram_phone=os.getenv("TELEGRAM_PHONE") or None,
        telegram_string_session=os.getenv("TELEGRAM_STRING_SESSION") or None,
        database_path=_sqlite_path(os.getenv("DATABASE_URL", "sqlite:///data/news.db")),
        channel_config_path=Path(os.getenv("CHANNEL_CONFIG", "config/channels.yaml")),
        timezone=os.getenv("TIMEZONE", "Asia/Seoul"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_target_chat_id=single_chat_id,
        telegram_target_chat_ids=multi_chat_ids,
    )


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.startswith("replace_with"):
        return None
    return text


def load_channels(path: Path) -> list[ChannelConfig]:
    if not path.exists():
        raise FileNotFoundError(
            f"Channel config not found: {path}. "
            "Copy config/channels.example.yaml to config/channels.yaml."
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    channels = raw.get("channels", [])
    result: list[ChannelConfig] = []

    for item in channels:
        name = str(item.get("name") or "").strip()
        username = _clean_optional(item.get("username"))
        invite_link = _clean_optional(item.get("invite_link"))

        if not username and not invite_link:
            continue

        result.append(
            ChannelConfig(
                name=name or username or "private_invite_channel",
                username=username,
                invite_link=invite_link,
                category=str(item.get("category") or "general"),
            )
        )

    return result
