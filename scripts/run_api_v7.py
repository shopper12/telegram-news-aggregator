from __future__ import annotations

import os
from typing import Any, Callable

import telegram_news.messenger_api
import uvicorn


def _optional_apply(import_path: str) -> Callable[[Any], Any]:
    module_name, _, attr = import_path.rpartition(".")
    try:
        module = __import__(module_name, fromlist=[attr])
        func = getattr(module, attr)
        if callable(func):
            return func
    except Exception as exc:
        print(f"[run_api_v7] optional patch skipped: {import_path} ({type(exc).__name__}: {exc})")
    return lambda api_module: api_module


for apply_patch in [
    _optional_apply("telegram_news.naver_quote_patch.apply"),
    _optional_apply("telegram_news.saju_news_patch.apply"),
    _optional_apply("telegram_news.unified_patch_v7.apply"),
    _optional_apply("telegram_news.chat_bridge.apply"),
]:
    apply_patch(telegram_news.messenger_api)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("telegram_news.messenger_api:app", host="0.0.0.0", port=port)
