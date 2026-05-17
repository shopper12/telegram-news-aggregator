from __future__ import annotations

import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    load_dotenv()

    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    phone = os.getenv("TELEGRAM_PHONE", "").strip()

    if not api_id_raw or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required in .env")
    if not phone:
        raise RuntimeError("TELEGRAM_PHONE=+8210xxxxxxxx is required in .env")

    client = TelegramClient(StringSession(), int(api_id_raw), api_hash)
    await client.start(phone=phone)

    session = client.session.save()
    await client.disconnect()

    print("\nCopy this value into GitHub Actions secret TELEGRAM_STRING_SESSION:\n")
    print(session)
    print("\nDo not commit this value to GitHub files. Store it only in GitHub Secrets.\n")


if __name__ == "__main__":
    asyncio.run(main())
