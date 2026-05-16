from __future__ import annotations

import requests


def _validate_bot_token(bot_token: str) -> None:
    if ":" not in bot_token:
        raise RuntimeError(
            "Invalid TELEGRAM_BOT_TOKEN. It must be the full BotFather token, "
            "for example 1234567890:AA... Do not use only the numeric bot id."
        )


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    _validate_bot_token(bot_token)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Telegram 메시지 길이 제한 대응
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]

    for chunk in chunks:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if resp.status_code == 404:
            raise RuntimeError(
                "Telegram bot send failed: 404 Not Found. "
                "Check TELEGRAM_BOT_TOKEN. It must be the full token from BotFather."
            )
        if resp.status_code in (400, 403):
            raise RuntimeError(
                f"Telegram bot send failed: HTTP {resp.status_code}. "
                "Check TELEGRAM_TARGET_CHAT_ID and send /start or any message to the bot first. "
                f"Response: {resp.text}"
            )
        resp.raise_for_status()
