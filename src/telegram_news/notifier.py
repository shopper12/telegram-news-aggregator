from __future__ import annotations

import re
import requests


def _validate_bot_token(bot_token: str) -> None:
    if ":" not in bot_token:
        raise RuntimeError(
            "Invalid TELEGRAM_BOT_TOKEN. It must be the full BotFather token, "
            "for example 1234567890:AA... Do not use only the numeric bot id."
        )


def _uses_html(text: str) -> bool:
    return '<a href=' in text.lower()


def _plain_text_from_html(text: str) -> str:
    # Telegram HTML 파싱 실패 시 최종 재시도용. 링크 태그는 종목명만 남긴다.
    text = re.sub(r'<a\s+href=["\'][^"\']+["\']\s*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</a>', '', text, flags=re.IGNORECASE)
    return text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')


def _post_message(bot_token: str, chat_id: str, chunk: str, *, html: bool) -> requests.Response:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": chunk,
        "disable_web_page_preview": True,
    }
    if html:
        payload["parse_mode"] = "HTML"
    return requests.post(url, json=payload, timeout=20)


def _raise_for_telegram_error(chat_id: str, resp: requests.Response) -> None:
    if resp.status_code == 404:
        raise RuntimeError(
            "Telegram bot send failed: 404 Not Found. "
            "Check TELEGRAM_BOT_TOKEN. It must be the full token from BotFather."
        )
    if resp.status_code in (400, 403):
        raise RuntimeError(
            f"Telegram bot send failed for chat_id={chat_id}: HTTP {resp.status_code}. "
            "Check the chat id and make sure that recipient sent /start or any message to the bot first. "
            "For a group chat, add the bot to the group first. "
            f"Response: {resp.text}"
        )
    resp.raise_for_status()


def _send_to_one_chat(bot_token: str, chat_id: str, text: str) -> None:
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]

    for chunk in chunks:
        html = _uses_html(chunk)
        resp = _post_message(bot_token, chat_id, chunk, html=html)

        # HTML 링크 렌더링이 깨진 경우에만 종목명 plain text로 1회 재시도한다.
        if html and resp.status_code == 400 and "can't parse entities" in resp.text.lower():
            resp = _post_message(bot_token, chat_id, _plain_text_from_html(chunk), html=False)

        _raise_for_telegram_error(chat_id, resp)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    _validate_bot_token(bot_token)
    _send_to_one_chat(bot_token, chat_id, text)


def send_telegram_message_to_many(bot_token: str, chat_ids: list[str], text: str) -> None:
    _validate_bot_token(bot_token)
    if not chat_ids:
        raise RuntimeError("No Telegram target chat IDs configured.")

    failures: list[str] = []
    success_count = 0
    for chat_id in chat_ids:
        try:
            _send_to_one_chat(bot_token, chat_id, text)
            success_count += 1
        except Exception as exc:
            failures.append(f"{chat_id}: {exc}")

    if failures:
        print("Telegram send warning: failed recipients:")
        for failure in failures:
            print(f"- {failure}")

    if success_count == 0:
        raise RuntimeError("Telegram send failed for all recipients:\n" + "\n".join(failures))

    print(f"Telegram send success: {success_count}/{len(chat_ids)} recipients")
