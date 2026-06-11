from __future__ import annotations

import os

import telegram_news.messenger_api
from telegram_news.quote_patch import apply as apply_quote_patch
import uvicorn

apply_quote_patch(telegram_news.messenger_api)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("telegram_news.messenger_api:app", host="0.0.0.0", port=port)
