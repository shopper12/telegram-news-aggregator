from __future__ import annotations

import os
import requests
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")
    if ":" not in token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN must be the full BotFather token, not only the numeric bot id")

    me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    if not me.ok:
        raise RuntimeError(f"getMe failed: HTTP {me.status_code} {me.text}")

    updates = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    if not updates.ok:
        raise RuntimeError(f"getUpdates failed: HTTP {updates.status_code} {updates.text}")

    data = updates.json()
    seen: set[int] = set()
    print("Bot:", me.json().get("result", {}).get("username"))
    print("\nKnown chats:")

    for update in data.get("result", []):
        message = update.get("message") or update.get("edited_message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or chat_id in seen:
            continue
        seen.add(chat_id)
        print(
            f"id={chat_id} type={chat.get('type')} "
            f"username={chat.get('username') or ''} "
            f"name={(chat.get('first_name') or chat.get('title') or '')} {(chat.get('last_name') or '')}".strip()
        )

    if not seen:
        print("No chats found. Ask each recipient to send /start to the bot first.")


if __name__ == "__main__":
    main()
