from __future__ import annotations

from typing import Any
import re

from . import bot_services as base
from .saju_news_patch import advanced_saju_text, telegram_news_text


HELP_TEXT = base.help_text().replace("명령어\n", "명령어 안내\n")


def apply(messenger_api: Any) -> None:
    original_answer = messenger_api.answer

    def patched_answer(message: str, user_id: str) -> str:
        body = messenger_api._strip_bot(message).strip()
        compact = body.replace(" ", "").lower()
        if compact in {"도움", "도움말", "help", "/help", "?"}:
            return HELP_TEXT
        if compact in {"뉴스", "/뉴스", "!뉴스", "news", "/news", "시황", "브리핑", "뉴스갱신", "뉴스새로고침", "새로고침", "뉴스업데이트", "refresh", "뉴스refresh"}:
            return telegram_news_text()[:3800]
        if body.startswith("사주") or body.startswith("운세"):
            return advanced_saju_text(user_id, body)[:3800]
        if body.startswith("타로"):
            question = re.sub(r"^타로\s*", "", body).strip()
            return base.tarot_reading(user_id, question)[:3800]
        return original_answer(message, user_id)

    messenger_api.answer = patched_answer
    messenger_api.API_VERSION = "messenger-stable-v7"
