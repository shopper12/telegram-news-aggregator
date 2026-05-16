from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv
import yaml


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    username: str
    category: str = "general"


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_name: str
    database_path: Path
    channel_config_path: Path
    timezone: str
    openai_api_key: str | None
    openai_model: str
    telegram_bot_token: str | None
    telegram_target_chat_id: str | None


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Only sqlite:/// DATABASE_URL is supported in this starter project.")
    return Path(database_url[len(prefix):])


def load_settings() -> Settings:
    load_dotenv()

    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    if not api_id_raw or not api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH are required. "
            "Create .env from .env.example first."
        )

    return Settings(
        telegram_api_id=int(api_id_raw),
        telegram_api_hash=api_hash,
        telegram_session_name=os.getenv("TELEGRAM_SESSION_NAME", "telegram_news_session").strip(),
        database_path=_sqlite_path(os.getenv("DATABASE_URL", "sqlite:///data/news.db")),
        channel_config_path=Path(os.getenv("CHANNEL_CONFIG", "config/channels.yaml")),
        timezone=os.getenv("TIMEZONE", "Asia/Seoul"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_target_chat_id=os.getenv("TELEGRAM_TARGET_CHAT_ID") or None,
    )


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
        username = str(item.get("username", "")).strip()
        if not username or username.startswith("replace_with"):
            continue
        result.append(
            ChannelConfig(
                name=str(item.get("name") or username),
                username=username,
                category=str(item.get("category") or "general"),
            )
        )

    return result
