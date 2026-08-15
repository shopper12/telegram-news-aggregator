from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_news import adaptive_strategy
from telegram_news import global_market_tracker
from telegram_news import market_dashboard_data
from telegram_news import market_dashboard_report
from telegram_news import market_note_formatter


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = ""
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _chart_payload(*, raw_closes, adjusted_closes, regular_price, chart_previous=50.0, previous=50.0):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": regular_price,
                        "chartPreviousClose": chart_previous,
                        "previousClose": previous,
                    },
                    "indicators": {
                        "quote": [{"close": raw_closes}],
                        "adjclose": [{"adjclose": adjusted_closes}],
                    },
                }
            ]
        }
    }


def test_dashboard_quote_uses_adjacent_daily_bars_not_chart_range_previous(monkeypatch):
    market_dashboard_data._yahoo_quote.cache_clear()
    payload = _chart_payload(
        raw_closes=[100.0, 110.0],
        adjusted_closes=[100.0, 110.0],
        regular_price=110.0,
        chart_previous=50.0,
        previous=100.0,
    )
    monkeypatch.setattr(market_dashboard_data.requests, "get", lambda *args, **kwargs: FakeResponse(payload))

    result = market_dashboard_data._yahoo_quote("EWY")

    assert round(result["change_pct"], 6) == 10.0
    assert result["change_pct"] != 120.0
    market_dashboard_data._yahoo_quote.cache_clear()


def test_dashboard_quote_records_latest_exchange_session_date(monkeypatch):
    market_dashboard_data._yahoo_quote.cache_clear()
    epoch = int(datetime(2026, 8, 14, 16, 0, tzinfo=ZoneInfo("America/New_York")).timestamp())
    payload = _chart_payload(
        raw_closes=[100.0, 101.0],
        adjusted_closes=[100.0, 101.0],
        regular_price=101.0,
        previous=100.0,
    )
    result = payload["chart"]["result"][0]
    result["timestamp"] = [epoch - 86400, epoch]
    result["meta"]["exchangeTimezoneName"] = "America/New_York"
    monkeypatch.setattr(market_dashboard_data.requests, "get", lambda *args, **kwargs: FakeResponse(payload))

    quote = market_dashboard_data._yahoo_quote("^GSPC")

    assert quote["session_date"] == "2026-08-14"
    market_dashboard_data._yahoo_quote.cache_clear()


def test_global_tracker_uses_adjusted_daily_bars_for_daily_and_multiday_returns(monkeypatch):
    payload = _chart_payload(
        raw_closes=[100.0, 102.0, 104.0, 106.0, 108.0, 110.0],
        adjusted_closes=[50.0, 51.0, 52.0, 53.0, 54.0, 55.0],
        regular_price=110.0,
        chart_previous=25.0,
        previous=108.0,
    )
    monkeypatch.setattr(global_market_tracker.requests, "get", lambda *args, **kwargs: FakeResponse(payload))

    result = global_market_tracker.fetch_asset_snapshot("QQQ")

    assert round(result["change_pct"], 6) == round((55.0 - 54.0) / 54.0 * 100.0, 6)
    assert round(result["return_5d"], 6) == 10.0
    assert result["change_pct"] != 340.0


def test_grounding_prose_is_normalized_in_second_non_search_call(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = []
    search_payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": "BLS reported PPI actual 0.2%, consensus 0.3%, previous 0.1%."}]},
                "groundingMetadata": {
                    "webSearchQueries": ["US PPI latest"],
                    "groundingChunks": [{"web": {"title": "BLS", "uri": "https://www.bls.gov/example"}}],
                },
            }
        ]
    }
    normalized_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": '{"macro_releases":[{"name":"PPI","released_at_kst":"","actual":0.2,"consensus":0.3,"previous":0.1,"unit":"%","surprise":"lower","market_relevance":""}],"upcoming_events":[],"earnings_and_guidance":[],"market_catalysts":[]}'
                        }
                    ]
                }
            }
        ]
    }

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse(search_payload if len(calls) == 1 else normalized_payload)

    monkeypatch.setattr(market_dashboard_report.requests, "post", fake_post)
    result, engine = market_dashboard_report._grounded_market_research(datetime(2026, 8, 14, 7, 30))

    assert len(calls) == 2
    assert calls[0]["tools"] == [{"google_search": {}}]
    assert "tools" not in calls[1]
    assert calls[1]["generationConfig"]["responseMimeType"] == "application/json"
    assert result["macro_releases"][0]["actual"] == 0.2
    assert result["sources"] == [{"title": "BLS", "uri": "https://www.bls.gov/example"}]
    assert engine.endswith(":normalized_json")


def test_explicit_symbol_recovery_prevents_false_no_symbol(monkeypatch):
    item = SimpleNamespace(
        title="미국 기업 섹터별 소식 정리",
        body="엔비디아 (NVDA) H100 임대 가격 상승. 메타 (META) 관련 소식.",
        categories=["us_stock"],
        tickers=[],
    )
    cluster = SimpleNamespace(best=lambda: SimpleNamespace(item=item))
    recovered = [
        SimpleNamespace(name="엔비디아", ticker="NVDA", asset_type="stock_us"),
        SimpleNamespace(name="메타", ticker="META", asset_type="stock_us"),
    ]
    monkeypatch.setattr(market_dashboard_report.display, "_display_symbols", lambda cluster: [])
    monkeypatch.setattr(market_dashboard_report, "resolve_symbols", lambda *args, **kwargs: recovered)

    symbols = market_dashboard_report._explicit_symbols(cluster)

    assert [symbol.ticker for symbol in symbols] == ["NVDA", "META"]


def test_source_grade_d_cannot_be_primary_catalyst(monkeypatch):
    cluster = object()
    monkeypatch.setattr(market_dashboard_report, "materiality_grade", lambda value: "D")

    assert market_dashboard_report._primary_catalyst_eligible(cluster) is False


def test_source_grade_a_can_be_primary_catalyst(monkeypatch):
    cluster = object()
    monkeypatch.setattr(market_dashboard_report, "materiality_grade", lambda value: "A")

    assert market_dashboard_report._primary_catalyst_eligible(cluster) is True


def test_local_fallback_demotes_weak_source_from_core_cause(monkeypatch):
    cluster = object()
    monkeypatch.setattr(market_dashboard_report, "materiality_score", lambda value: 99)
    monkeypatch.setattr(market_dashboard_report, "materiality_grade", lambda value: "D")
    monkeypatch.setattr(market_dashboard_report.display, "_display_title", lambda value, limit: "미국 기업 섹터별 소식 정리")
    payload = {
        "generated_at_iso": "2026-08-14T07:30:00+09:00",
        "market": {
            "global_market_quotes": [],
            "korea_proxies": [],
            "sector_baskets": {},
            "risk_regime": {"regime": "NEUTRAL"},
            "supply_demand_line": "수급 확인불가",
            "market_bias": "중립",
        },
        "grounded_research": {},
        "quality": {"grounding_engine": "test"},
    }

    text = market_dashboard_report._local(payload, [cluster], rule="test")

    assert "출처 A/B로 검증된 핵심 촉매 없음" in text
    assert "보조 관찰 [중요도 99 · 출처 D]" in text
    assert "1) [중요도 99 · 출처 D]" not in text


def test_stale_us_session_is_blocked_before_grounding_or_gemini(monkeypatch):
    fixed = datetime(2026, 8, 18, 7, 30, tzinfo=ZoneInfo("Asia/Seoul"))  # Tuesday KST -> expected Monday US

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(market_dashboard_report, "datetime", FixedDateTime)
    monkeypatch.setattr(market_dashboard_report.strict, "_select_strict", lambda summaries: ([], 0, 0, "test", 0))
    monkeypatch.setattr(market_dashboard_report.display, "_drop_noise", lambda selected: selected)
    monkeypatch.setattr(
        market_dashboard_report,
        "get_market_dashboard_context",
        lambda: {"us_session_date": "2026-08-14"},
    )
    monkeypatch.setattr(
        market_dashboard_report,
        "_grounded_market_research",
        lambda now: (_ for _ in ()).throw(AssertionError("grounding must not run for stale session")),
    )
    monkeypatch.setattr(
        market_dashboard_report,
        "_gemini",
        lambda payload: (_ for _ in ()).throw(AssertionError("Gemini must not run for stale session")),
    )

    report = market_dashboard_report.build_us_close_dashboard([], hours=12)

    assert "휴장·데이터 미갱신" in report
    assert "2026-08-17" in report
    assert "2026-08-14" in report
    assert "이전 거래일 마감 데이터를 오늘 마감처럼 재전송하지 않습니다" in report


def test_session_freshness_accepts_expected_prior_us_date():
    now = datetime(2026, 8, 18, 7, 30, tzinfo=ZoneInfo("Asia/Seoul"))

    fresh, expected, actual = market_dashboard_report._session_freshness(
        {"us_session_date": "2026-08-17"}, now
    )

    assert fresh is True
    assert expected == actual == "2026-08-17"


def test_adaptive_us_close_runs_learning_without_inserting_visible_section(monkeypatch):
    original_report = "🇺🇸 08/16 미국증시 마감 → 🇰🇷 한국장 프리뷰\n📊 주요 지수\n✅ 3줄 요약"
    monkeypatch.setenv("BRIEFING_KIND", "us_close")
    monkeypatch.setattr(adaptive_strategy.base_report, "build_markdown_report", lambda *args, **kwargs: original_report)
    monkeypatch.setattr(adaptive_strategy.base_report.s, "_select_strict", lambda summaries: ([], 0, 0, "test", 0))
    monkeypatch.setattr(adaptive_strategy.base_report, "_drop_noise", lambda selected: selected)
    called = {"count": 0}

    def fake_cycle(*args, **kwargs):
        called["count"] += 1
        return "🧠 지속학습 상태\n  • 내부 상태"

    monkeypatch.setattr(adaptive_strategy, "run_adaptive_cycle", fake_cycle)
    adaptive_strategy.install()

    result = adaptive_strategy.base_report.build_markdown_report([], 12, "Asia/Seoul")

    assert called["count"] == 1
    assert result == original_report
    assert "지속학습" not in result


def test_market_note_formatter_does_not_reformat_us_close(monkeypatch):
    original_report = "🇺🇸 08/16 미국증시 마감 → 🇰🇷 한국장 프리뷰\n📊 주요 지수\n✅ 3줄 요약"
    monkeypatch.setenv("BRIEFING_KIND", "us_close")
    monkeypatch.setattr(market_note_formatter.base_report, "build_markdown_report", lambda *args, **kwargs: original_report)
    monkeypatch.setattr(market_note_formatter.base_report, "get_market_context", lambda: None)
    monkeypatch.setattr(market_note_formatter, "_INSTALLED", False)
    market_note_formatter.install()

    result = market_note_formatter.base_report.build_markdown_report([], 12, "Asia/Seoul")

    assert result == original_report
    assert "글로벌 장전 브리핑" not in result


def test_local_fallback_labels_importance_and_source_grade_and_hides_internal_failure_text():
    payload = {
        "generated_at_iso": "2026-08-14T07:30:00+09:00",
        "market": {
            "global_market_quotes": [{"label": "VIX", "price": 15.0, "change_pct": -3.0}],
            "korea_proxies": [],
            "sector_baskets": {},
            "risk_regime": {"regime": "NEUTRAL"},
            "supply_demand_line": "수급 확인불가",
            "market_bias": "중립",
        },
        "grounded_research": {},
        "quality": {"grounding_engine": "grounding_json_normalize_failed:test"},
    }

    text = market_dashboard_report._local(payload, clusters=[], rule="test")

    assert "Gemini 최종 인과분석 실패" not in text
    assert "변동성·달러·금리" in text
    assert "Google grounding 미사용(grounding_json_normalize_failed:test)" in text
