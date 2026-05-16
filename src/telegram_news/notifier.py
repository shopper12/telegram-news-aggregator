from __future__ import annotations

import requests


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
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
        resp.raise_for_status()
