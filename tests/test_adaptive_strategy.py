from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_news.adaptive_strategy import _insert_strategy, _summary_news, generate_recommendations
from telegram_news.strategy_learning import (
    adapt_model_from_results,
    default_state,
    evaluate_open_recommendations,
    update_news_memory,
)


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 24, 7, 0, tzinfo=KST)


def _asset(price, change, ret5, ret20, vol=2.0):
    return {"price": price, "change_pct": change, "return_5d": ret5, "return_20d": ret20, "volatility_20d": vol}


def test_news_memory_deduplicates_and_counts_repeated_issue():
    event = {"signature": "same", "title": "반도체 수주 확대", "sectors": ["반도체"], "materiality": 90, "sentiment": 2, "first_seen": NOW.isoformat(), "last_seen": NOW.isoformat(), "count": 1}
    memory = {"events": []}
    update_news_memory(memory, [event], NOW)
    update_news_memory(memory, [event], NOW + timedelta(minutes=30))
    assert len(memory["events"]) == 1
    assert memory["events"][0]["count"] == 2


def test_all_summaries_are_converted_before_strict_display_filtering():
    summaries = [
        SimpleNamespace(
            title="아직 화면 선별 전인 반도체 공급 뉴스",
            body="HBM 공급 계약 확대와 실적 개선 기대",
            judgment="정보성",
            risk="과장 가능성 확인",
            sectors=["반도체"],
            keywords=["HBM", "공급"],
            tickers=["SOXX"],
            importance_score=35,
            repeat_count=2,
            message_dates=[NOW.isoformat()],
        )
    ]
    events = _summary_news(summaries, NOW)
    assert len(events) == 1
    assert events[0]["title"].startswith("아직 화면 선별 전")
    assert events[0]["count"] == 2
    assert "HBM" in events[0]["keywords"]
    assert events[0]["materiality"] == 35


def test_generate_recommendations_combines_regime_momentum_and_news():
    memory = {"events": [{"signature": "semi", "title": "HBM 공급 계약과 실적 개선", "sectors": ["반도체", "AI인프라"], "keywords": ["HBM"], "tickers": ["SOXX"], "materiality": 100, "sentiment": 3, "last_seen": NOW.isoformat(), "count": 2}]}
    snapshot = {
        "regime": "risk_on",
        "assets": {
            "SOXX": _asset(300.0, 1.2, 4.0, 10.0),
            "QQQ": _asset(600.0, 0.8, 2.0, 6.0),
            "GLD": _asset(250.0, -0.2, -1.0, 2.0),
            "^VIX": _asset(15.0, -5.0, -8.0, -12.0),
            "^TNX": _asset(4.1, -0.5, -1.0, 1.0),
        },
    }
    recommendations = generate_recommendations(snapshot, memory, default_state(), "morning", NOW)
    assert recommendations
    assert recommendations[0]["ticker"] == "SOXX"
    assert recommendations[0]["target_price"] > recommendations[0]["entry_price"]
    assert recommendations[0]["stop_price"] < recommendations[0]["entry_price"]
    assert recommendations[0]["components"]["news"] > 0


def test_evaluation_and_online_weight_update_use_24h_result_once():
    ledger = {"recommendations": [{"id": "r1", "created_at": (NOW - timedelta(hours=25)).isoformat(), "ticker": "SOXX", "entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0, "status": "open", "evaluations": {}, "learned_24h": False, "components": {"momentum": 2.0, "regime": 1.0, "news": 1.5, "defensive": -0.5}}]}
    snapshot = {"assets": {"SOXX": {"price": 104.0}}}
    state = default_state()
    updates = evaluate_open_recommendations(ledger, snapshot, NOW)
    learned = adapt_model_from_results(state, ledger, NOW)
    learned_again = adapt_model_from_results(state, ledger, NOW)
    assert any(item["horizon"] == "24h" for item in updates)
    assert learned == 1 and learned_again == 0
    assert state["stats"]["wins_24h"] == 1
    assert state["weights"]["momentum"] > 1.0
    assert state["weights"]["defensive"] < 1.0


def test_insert_strategy_before_news_selection_line():
    report = "\n".join(["📊 뉴스", "수급/시장: 중립", "선별방식: 중요도", "📌 핵심 이슈"])
    merged = _insert_strategy(report, "🌐 글로벌 시황\n🧠 지속학습")
    assert merged.index("🌐 글로벌 시황") < merged.index("선별방식")
    assert merged.index("🧠 지속학습") < merged.index("📌 핵심 이슈")
