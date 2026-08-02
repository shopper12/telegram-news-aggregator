from types import SimpleNamespace

from telegram_news import consistency_rules as rules
from telegram_news.strategy_learning import default_state


class FakeCluster:
    def __init__(self, title, body, news_type, urls=None, channels=1, item_count=1):
        item = SimpleNamespace(title=title, body=body, source_urls=urls or [])
        self._best = SimpleNamespace(item=item, news_type=news_type)
        self.items = [SimpleNamespace(item=item) for _ in range(item_count)]
        self._channels = channels

    def best(self):
        return self._best

    def channel_count(self):
        return self._channels


def _snapshot(regime, qqq=0.0, eem=0.0, hyg=0.0, gld=0.0, tlt=0.0):
    return {
        "regime": regime,
        "assets": {
            "QQQ": {"change_pct": qqq},
            "EEM": {"change_pct": eem},
            "HYG": {"change_pct": hyg},
            "GLD": {"change_pct": gld},
            "TLT": {"change_pct": tlt},
        },
    }


def test_four_axis_rule_blocks_bullish_label_in_crash():
    outlook = rules.consistent_infer_market_outlook(
        phase="장중",
        news_inputs=[{"title": "수주 확대", "text": "실적 개선과 공급 증가", "materiality": 100}],
        sectors=["반도체"],
        market_context={
            "kospi_change_pct": -6.0,
            "kosdaq_change_pct": -11.0,
            "sp500_change_pct": -4.0,
            "nasdaq_change_pct": -5.0,
        },
        global_snapshot=_snapshot("risk_off", -4.0, -5.0, -4.5, -1.0, -2.0),
    )
    assert outlook.score <= -5
    assert outlook.verdict == "하방 우세 / 위험회피 모드"
    assert "축1 지수(30%)" in outlook.evidence_line
    assert "축4 뉴스(20%)" in outlook.evidence_line


def test_four_axis_rule_allows_bullish_label_only_above_five():
    outlook = rules.consistent_infer_market_outlook(
        phase="장전",
        news_inputs=[{"title": "실적 서프라이즈", "text": "상향 증가 확대 수주", "materiality": 95}],
        sectors=["반도체", "AI인프라"],
        market_context={
            "kospi_change_pct": 2.5,
            "kosdaq_change_pct": 3.0,
            "sp500_change_pct": 2.2,
            "nasdaq_change_pct": 3.5,
        },
        global_snapshot=_snapshot("risk_on", 1.0, 1.0, 0.5, 0.2, 0.1),
    )
    assert outlook.score >= 5
    assert outlook.verdict == "상방 우세"


def test_metric_acronyms_are_never_returned_as_tickers(monkeypatch):
    monkeypatch.setattr(
        rules,
        "_ORIGINAL_RESOLVE_SYMBOLS",
        lambda *args, **kwargs: [
            SimpleNamespace(name="ARR", ticker="ARR", asset_type="stock_us"),
            SimpleNamespace(name="RPO", ticker="RPO", asset_type="stock_us"),
            SimpleNamespace(name="ServiceNow", ticker="NOW", asset_type="stock_us"),
        ],
    )
    result = rules._safe_resolve_symbols("ServiceNow RPO와 OpenAI ARR 증가")
    assert [item.ticker for item in result] == ["NOW"]


def test_materiality_score_and_source_grade_are_separate(monkeypatch):
    cluster = FakeCluster(
        "FOMC 결정",
        "기준금리 결정으로 시장 방향 전환",
        "거시",
        urls=["https://source-a.example/a", "https://source-b.example/b"],
        channels=2,
        item_count=2,
    )
    monkeypatch.setattr(rules, "_ORIGINAL_MATERIALITY_SCORE", lambda _cluster: 95)
    assert rules.consistent_materiality_score(cluster) == 100
    assert rules.consistent_materiality_grade(cluster) == "A"


def test_report_caps_perfect_a_and_marks_unmapped_stocks():
    report = "\n".join([
        "1) [100/A] 첫째",
        "  • 관련종목: 직접 언급 없음",
        "2) [100/A] 둘째",
        "3) [100/A] 셋째",
        "  • 관련종목: ServiceNow(NOW)",
    ])
    formatted = rules._format_related_report(report)
    assert formatted.count("[100/A]") == 2
    assert "[99/A] 셋째" in formatted
    assert "직접 언급 없음 (국내 수혜주 추정 불가)" in formatted
    assert "ServiceNow(NOW, NYSE)" in formatted
    assert "국내 ETF 연계" in formatted


def test_empty_learning_state_is_hidden_until_strategy_exists():
    snapshot = {
        "regime_label": "혼조",
        "regime_score": 0.0,
        "regions": "확인불가",
        "flow_proxy": "확인불가",
        "data_quality": 0,
        "requested_assets": 19,
    }
    state = default_state()
    empty = rules._consistent_build_strategy_section(
        snapshot,
        {"events": []},
        state,
        {"recommendations": []},
        None,
        [],
        [],
        0,
    )
    assert "🧠 지속학습 상태" not in empty
    assert "승률 0.0%" not in empty

    active = rules._consistent_build_strategy_section(
        snapshot,
        {"events": []},
        state,
        {"recommendations": [{"status": "open"}]},
        None,
        [],
        [],
        0,
    )
    assert "🧠 지속학습 상태" in active
