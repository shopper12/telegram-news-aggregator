from __future__ import annotations

import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id_raw = os.getenv("TELEGRAM_API_ID") or input("TELEGRAM_API_ID: ").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH") or getpass.getpass("TELEGRAM_API_HASH: ").strip()
    phone = os.getenv("TELEGRAM_PHONE") or input("TELEGRAM_PHONE, e.g. +8210xxxxxxxx: ").strip()

    if not api_id_raw or not api_hash or not phone:
        raise SystemExit("TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE are required.")

    client = TelegramClient(StringSession(), int(api_id_raw), api_hash)
    await client.start(phone=phone)
    session = client.session.save()
    await client.disconnect()

    print("\nTELEGRAM_STRING_SESSION generated. Copy only the value below into GitHub Secrets.")
    print("Do not commit it and do not paste it into chat.")
    print("\n" + session + "\n")


if __name__ == "__main__":
    asyncio.run(main())
