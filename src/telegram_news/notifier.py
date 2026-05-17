from __future__ import annotations

import requests


def _validate_bot_token(bot_token: str) -> None:
    if ":" not in bot_token:
        raise RuntimeError(
            "Invalid TELEGRAM_BOT_TOKEN. It must be the full BotFather token, "
            "for example 1234567890:AA... Do not use only the numeric bot id."
        )


def _send_to_one_chat(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Telegram 메시지 길이 제한 대응. parse_mode를 쓰지 않는다.
    # 리포트 안의 _, [, ], -, # 등이 Markdown 파싱 오류를 만들 수 있기 때문이다.
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]

    for chunk in chunks:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
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
                f"Telegram bot send failed for chat_id={chat_id}: HTTP {resp.status_code}. "
                "Check the chat id and make sure that recipient sent /start or any message to the bot first. "
                f"Response: {resp.text}"
            )
        resp.raise_for_status()


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    _validate_bot_token(bot_token)
    _send_to_one_chat(bot_token, chat_id, text)


def send_telegram_message_to_many(bot_token: str, chat_ids: list[str], text: str) -> None:
    _validate_bot_token(bot_token)
    if not chat_ids:
        raise RuntimeError("No Telegram target chat IDs configured.")

    failures: list[str] = []
    for chat_id in chat_ids:
        try:
            _send_to_one_chat(bot_token, chat_id, text)
        except Exception as exc:
            failures.append(f"{chat_id}: {exc}")

    if failures:
        raise RuntimeError("Telegram send failed for one or more recipients:\n" + "\n".join(failures))
