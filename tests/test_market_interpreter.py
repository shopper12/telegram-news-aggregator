"""Grounded market chatbot behavior when the LLM has no API credentials."""
from datetime import datetime, timezone

from telegram_news.market_interpreter import interpret_market
from telegram_news import crisis_chat


def _quote(symbol, daily, five, *, opened=True):
    return {"symbol": symbol, "name": symbol, "price": 100.0,
            "day_pct": daily, "m5_pct": five, "m15_pct": -1.0,
            "fresh": True, "open": opened, "at": "2026-11-05T19:30:00+00:00",
            "age_sec": 0}


def _snapshot():
    quotes = {
        "SPY": _quote("SPY", -4.09, 0.03),
        "QQQ": _quote("QQQ", -5.84, 0.00),
        "SOXX": _quote("SOXX", -10.65, -0.13),
        "NVDA": _quote("NVDA", -8.86, -0.17),
        "TSLA": _quote("TSLA", -11.3, -0.22),
        "TLT": _quote("TLT", 0.43, -0.02),
        "GLD": _quote("GLD", 0.86, 0.03),
        "HYG": _quote("HYG", -1.2, 0.0),
    }
    return {"checked_at": "2026-11-06T04:30:37+09:00", "quotes": quotes}


def test_fallback_is_decision_useful_not_one_line():
    data = _snapshot()
    news = [{"title": "Reuters: Qualcomm earnings weak, Nasdaq chip stocks tumble as AI data center rules reviewed",
             "published_at": "2026-11-05T18:00:00+00:00", "link": "https://example.org/report"}]
    out = interpret_market("왜 떨어져? 지금 팔아?", data, news)
    for term in ("4.09%", "5.84%", "10.65%", "4.81%p",
                 "반도체", "안전자산", "원인 후보", "매도/매수 확정 지시는 불가",
                 "최근 5분", "퀄컴"):
        assert term in out, term
    assert out.index("⑥ 대응 기준") < out.index("최근 관련 보도")


def test_no_valid_quotes_no_confident_diagnosis():
    data = _snapshot()
    for q in data["quotes"].values():
        q["fresh"] = False
    out = interpret_market("왜 떨어져?", data, [])
    assert "유효 정규장 시세 부족" in out
    assert "자료 없음" in out or "조회 불가" in out
    assert "동반 하락이므로" not in out


def test_missing_ai_key_reports_reason(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    answer, status = crisis_chat._model_answer("왜 폭락?", _snapshot(), [])
    assert answer is None and status == "미설정"


def test_bot_response_has_analysis_and_no_fake_zero_second_live_claim(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(crisis_chat, "scan", lambda now: _snapshot())
    monkeypatch.setattr(crisis_chat, "_headlines", lambda now: [])
    monkeypatch.setattr(crisis_chat, "_chat_ids", lambda: ["12345"])
    text = crisis_chat.answer("봇 시장 지금 팔아?", "12345")
    assert "제공봉" in text
    assert "자료 0초 전" not in text
    assert "성장·기술주 매도 집중" in text
    assert "AI 추가분석 미적용" in text
    assert "\\n" not in text
    assert text.count("\n") >= 8
