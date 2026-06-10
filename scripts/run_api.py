from __future__ import annotations

import os

import telegram_news.api_server
from telegram_news.runtime_patches import apply as apply_runtime_patches
import uvicorn

apply_runtime_patches(telegram_news.api_server)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("telegram_news.api_server:app", host="0.0.0.0", port=port)
