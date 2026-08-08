from __future__ import annotations

import os
import threading
from typing import Any, Callable

# Kakao MessengerBotR must be able to return the same full report body that
# Telegram receives. Keep its response limit aligned with the report limit.
os.environ["MESSENGER_NOTE_MAX_CHARS"] = os.getenv("MAX_REPORT_CHARS", "12000")

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


def _install_fast_news_path(api_module: Any) -> None:
    """Make `봇 뉴스` responsive before optional/heavy patches finish loading."""
    original_news = api_module._news

    def cached_news_first() -> str:
        try:
            data = api_module.load_latest_report()
            report = str(data.get("report") or "").strip()
            if report:
                return report
        except Exception as exc:
            print(f"[run_api_v7] fast cached news unavailable: {type(exc).__name__}: {exc}")
        return original_news()

    api_module._news = cached_news_first


def _install_runtime_patches(api_module: Any) -> None:
    """Install non-critical patches after the HTTP server can already answer."""
    try:
        from telegram_news.consistency_rules import install as install_consistency_rules
        from telegram_news.continuous_quote_fallback import install as install_continuous_quotes
        from telegram_news.continuous_quote_fallback import install_messenger_quote_fallback
        from telegram_news.market_note_formatter import install_dispatch_hook, install_messenger_bridge

        install_continuous_quotes()
        install_consistency_rules()
        install_dispatch_hook()

        for apply_patch in [
            _optional_apply("telegram_news.naver_quote_patch.apply"),
            _optional_apply("telegram_news.saju_news_patch.apply"),
            _optional_apply("telegram_news.unified_patch_v7.apply"),
            _optional_apply("telegram_news.chat_bridge.apply"),
            _optional_apply("telegram_news.telegram_webhook.apply"),
        ]:
            apply_patch(api_module)

        install_messenger_quote_fallback(api_module)
        install_messenger_bridge(api_module)
        api_module.RUNTIME_PATCH_STATUS = "ready"
        print("[run_api_v7] runtime patches ready")
    except Exception as exc:
        api_module.RUNTIME_PATCH_STATUS = f"failed:{type(exc).__name__}"
        print(f"[run_api_v7] runtime patch install failed: {type(exc).__name__}: {exc}")


def _start_runtime_patches(api_module: Any) -> threading.Thread:
    thread = threading.Thread(
        target=_install_runtime_patches,
        args=(api_module,),
        name="telegram-news-runtime-patches",
        daemon=True,
    )
    thread.start()
    return thread


_install_fast_news_path(telegram_news.messenger_api)
telegram_news.messenger_api.RUNTIME_PATCH_STATUS = "starting"


if __name__ == "__main__":
    # Render free instances can cold-start slowly. Open the HTTP port first;
    # consistency/strategy/webhook patches are installed concurrently afterwards.
    _start_runtime_patches(telegram_news.messenger_api)
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(telegram_news.messenger_api.app, host="0.0.0.0", port=port)
