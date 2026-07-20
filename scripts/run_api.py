from __future__ import annotations

import os

import telegram_news.messenger_api
from telegram_news.naver_quote_patch import apply as apply_quote_patch
from telegram_news.saju_news_patch import apply as apply_saju_news_patch
from telegram_news.unified_patch_v7 import apply as apply_unified_patch
import uvicorn


apply_quote_patch(telegram_news.messenger_api)
apply_saju_news_patch(telegram_news.messenger_api)
apply_unified_patch(telegram_news.messenger_api)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("telegram_news.messenger_api:app", host="0.0.0.0", port=port)
