from __future__ import annotations


def apply(api_module) -> None:
    """Add manual refresh handling without replacing the canonical news handler.

    News rendering is owned by the Messenger/market-note bridge. Keeping a second
    `_news()` implementation here caused Kakao to prepend a Telegram-only wrapper,
    truncate the report to 1,400 characters, and sometimes surface a stale Render
    cache instead of the same report Telegram receives.
    """
    original_answer = api_module.answer

    def _manual_refresh() -> str:
        try:
            from .telegram_dispatch import generate_and_send_latest_report

            return generate_and_send_latest_report(
                hours=1,
                limit=999,
                briefing_kind="manual",
                collect=True,
                source="telegram_manual",
            )[:1400]
        except Exception as exc:
            return f"뉴스갱신 실패: {type(exc).__name__}: {exc}"

    def _patched_answer(message: str, user_id: str) -> str:
        body = api_module._strip_bot(message)
        low = body.replace(" ", "").lower()
        if low in {"뉴스갱신", "뉴스새로고침", "새로고침", "뉴스업데이트", "refresh", "뉴스refresh", "/뉴스갱신", "새뉴스"}:
            return _manual_refresh()
        return original_answer(message, user_id)

    api_module.answer = _patched_answer
    api_module.API_VERSION = "messenger-telegram-source-v7"
