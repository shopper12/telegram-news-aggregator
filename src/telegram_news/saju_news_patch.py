from __future__ import annotations


def apply(api_module) -> None:
    original_answer = api_module.answer

    def _telegram_news_only() -> str:
        try:
            data = api_module.load_latest_report()
            generated = data.get("generated_at") or "시간미상"
            source = data.get("source") or "telegram_collection"
            report = str(data.get("report") or "").strip()
            if not report:
                return "최신 텔레그램 뉴스 리포트가 없습니다. GitHub Actions 수집 작업을 확인하세요."
            return (
                "📰 텔레그램 뉴스 종합\n"
                "소스: 연결된 텔레그램 채널/수집 리포트\n"
                f"생성: {generated}\n"
                f"수집경로: {source}\n\n"
                f"{report}"
            )[:1400]
        except Exception as exc:
            return f"뉴스 읽기 실패: {type(exc).__name__}. 텔레그램 수집 리포트 생성 상태를 확인하세요."

    def _manual_refresh() -> str:
        try:
            from .app import generate_report
            return generate_report(hours=1, limit=999, briefing_kind="manual", collect=True, send=False, source="telegram_manual")[:1400]
        except Exception as exc:
            return f"뉴스갱신 실패: {type(exc).__name__}: {exc}"

    def _patched_answer(message: str, user_id: str) -> str:
        body = api_module._strip_bot(message)
        low = body.replace(" ", "").lower()
        if low in {"뉴스갱신", "뉴스새로고침", "새로고침", "뉴스업데이트", "refresh", "뉴스refresh", "/뉴스갱신", "새뉴스"}:
            return _manual_refresh()
        return original_answer(message, user_id)

    api_module._live_news = lambda: None
    api_module._news = _telegram_news_only
    api_module.answer = _patched_answer
    api_module.API_VERSION = "messenger-telegram-source-v5"
