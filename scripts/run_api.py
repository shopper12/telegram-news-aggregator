from __future__ import annotations

import os

import telegram_news.api_extended_v5
import uvicorn


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("telegram_news.api_extended_v5:app", host="0.0.0.0", port=port)
