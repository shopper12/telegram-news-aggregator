from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_news import global_market_tracker
from telegram_news import market_dashboard_report as report
from telegram_news import notifier
from telegram_news import report_integrity


KST = ZoneInfo("Asia/Seoul")


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _asset(price, change, session_date="2026-08-18"):
    return {
        "price": price,
        "change_pct": change,
        "return_5d": 0.0,
        "return_20d": 0.0,
        "volatility_20d": 1.0,
        "session_date": session_date,
        "timestamp": "2026-08-19T07:30:00+09:00",
        "error": None,
    }


def _atomic_context():
    assets = {
        "^DJI": _asset(53343.40, -0.22),
        "^GSPC": _asset(7691.76, -0.69),
        "^IXIC": _asset(26289.71, -1.33),
        "^RUT": _asset(2400.0, -1.10),
        "^SOX": _asset(6500.0, -4.98),
        "DX-Y.NYB": _asset(98.2, 0.1),
        "^VIX": _asset(16.0, 4.2),
        "^TNX": _asset(4.706, -0.02),
        "^TYX": _asset(5.285, -0.02),
        "CL=F": _asset(84.42, -0.09),
        "BZ=F": _asset(91.27, 0.44),
        "GC=F": _asset(4389.50, -0.64),
        "SI=F": _asset(63.420, -4.08),
        "EWY": _asset(130.0, -2.4),
        "KORU": _asset(44.0, -6.5),
        "SOXX": _asset(390.0, -5.0),
        "NVDA": _asset(180.0, -2.34),
        "MU": _asset(970.0, -7.02),
        "AMD": _asset(220.0, -4.0),
        "INTC": _asset(100.0, -6.58),
        "CRM": _asset(300.0, 2.71),
        "NOW": _asset(950.0, 1.52),
        "MSFT": _asset(600.0, 0.27),
        "LLY": _asset(1000.0, 3.60),
        "JNJ": _asset(200.0, 3.33),
        "PG": _asset(180.0, 1.1),
        "AAPL": _asset(300.0, 1.45),
        "META": _asset(800.0, -4.45),
    }
    market = report_integrity.MarketSnapshot(
        report_kind="us_close",
        captured_at=datetime(2026, 8, 19, 7, 30, tzinfo=KST),
        market_context={
            "usd_krw": 1413.0,
            "supply_demand_line": "수급 확인불가",
            "market_bias": "방어 우위",
        },
        global_snapshot={"assets": assets, "regime": "risk_off", "regime_label": "위험회피"},
        index_values={
            "dow_change_pct": -0.22,
            "sp500_change_pct": -0.69,
            "nasdaq_change_pct": -1.33,
            "russell2000_change_pct": -1.10,
            "sox_change_pct": -4.98,
            "kospi_change_pct": None,
            "kosdaq_change_pct": None,
        },
        allowed_index_fields=report_integrity.US_INDEX_FIELDS,
    )
    news = report_integrity.NewsPool(
        summaries=(), selected=(), displayed=(), suppressed_count=0,
        stock_count=0, blocked_count=0, pre_gate_count=0, rule="test",
    )
    return report_integrity.ReportRunContext(market=market, news=news, hours=12, timezone_name="Asia/Seoul")


def _sample_style_report():
    return "\n".join([
        "[2026년 8월 19일 모닝 시황]",
        "국채금리 부담에 반도체 약세, 소프트웨어·방어주로 순환매",
        "",
        "──────────",
        "<요약>",
        "미국 주요 지수는 하락 마감",
        "장기금리 부담으로 기술주 투자심리 위축",
        "반도체 약세와 방어주 강세가 동시에 관찰",
        "",
        "──────────",
        "<주요 지수 종합>",
        "다우존스: 53,343.40 (-0.22%)",
        "S&P 500: 7,691.76 (-0.69%)",
        "Nasdaq: 26,289.71 (-1.33%)",
        "",
        "──────────",
        "<경제지표>",
        "검증된 주요 경제지표 발표 없음",
        "",
        "──────────",
        "<매크로>",
        "1. 장기금리 부담",
        "미국 장기금리의 높은 레벨이 성장주 밸류에이션에 부담으로 작용",
        "",
        "──────────",
        "<원자재>",
        "(최근 미국 현지 정산가/일봉 snapshot 기준, 이후 실시간 변동 가능)",
        "1. 금, 은 가격",
        "금 $4,389.50 (-0.64%), 은 $63.420 (-4.08%)",
        "",
        "2. 유가",
        "WTI $84.42 (-0.09%), 브렌트유 $91.27 (+0.44%)",
        "",
        "──────────",
        "<주요 테마>",
        "[반도체·메모리]",
        "1. Micron (-7.02%)",
        "동종 메모리·반도체 약세가 동반됨",
    ])


def test_prompt_locks_requested_morning_note_structure_and_depth():
    text = report._prompt({"market": {}, "grounded_research": {}, "news_issues": []})
    for section in ["<요약>", "<주요 지수 종합>", "<경제지표>", "<매크로>", "<원자재>", "<주요 테마>"]:
        assert section in text
    assert "[YYYY년 M월 D일 모닝 시황]" in text
    assert "POSITIONING" not in text  # final format stays Korean/user-facing
    assert "증권사의 '매수 의견/목표가'" in text
    assert "2~4문장" in text
    assert "이모지는 쓰지 않는다" in text


def test_audit_accepts_new_format_and_rejects_old_dashboard_format():
    ok, reason = report._audit(_sample_style_report())
    assert ok is True
    assert reason == "pass"

    old = "🇺🇸 08/19 미국증시 마감 → 🇰🇷 한국장 프리뷰\n📊 주요 지수\n🚀 주도 섹터\n✅ 3줄 요약"
    ok, reason = report._audit(old)
    assert ok is False
    assert reason in {"bad_header", "missing_section:<요약>"}


def test_local_fallback_uses_requested_sections_and_rich_atomic_prices():
    context = _atomic_context()
    with report_integrity.activate_context(context):
        market = report._active_atomic_market_context()

    payload = {
        "generated_at_iso": "2026-08-19T07:30:00+09:00",
        "market": market,
        "grounded_research": {},
        "quality": {"grounding_engine": "test"},
    }
    text = report._local(payload, [], "test")

    assert text.startswith("[2026년 8월 19일 모닝 시황]")
    assert "<요약>" in text
    assert "<주요 지수 종합>" in text
    assert "S&P 500: 7,691.76 (-0.69%)" in text
    assert "미국 30년물: 5.2850% (-0.02%)" in text
    assert "<원자재>" in text
    assert "금: $4,389.50 (-0.64%)" in text
    assert "브렌트유: $91.270 (+0.44%)" in text
    assert "<주요 테마>" in text
    assert "[반도체·메모리]" in text
    assert "Google grounding" not in text
    assert "Gemini진단" not in text


def test_active_market_context_reuses_atomic_snapshot_without_dashboard_refetch(monkeypatch):
    context = _atomic_context()
    monkeypatch.setattr(
        report,
        "get_market_dashboard_context",
        lambda: (_ for _ in ()).throw(AssertionError("must not refetch market data inside us-close report")),
    )
    with report_integrity.activate_context(context):
        market = report._active_atomic_market_context()

    assert market["snapshot_atomic"] is True
    spx = next(row for row in market["global_market_quotes"] if row["label"] == "S&P500")
    assert spx["change_pct"] == -0.69
    assert market["us_session_date"] == "2026-08-18"


def test_global_tracker_records_exchange_local_session_date(monkeypatch):
    epoch = int(datetime(2026, 8, 18, 16, 0, tzinfo=ZoneInfo("America/New_York")).timestamp())
    payload = {
        "chart": {
            "result": [{
                "meta": {
                    "regularMarketPrice": 101.0,
                    "previousClose": 100.0,
                    "exchangeTimezoneName": "America/New_York",
                },
                "timestamp": [epoch - 86400, epoch],
                "indicators": {
                    "quote": [{"close": [100.0, 101.0]}],
                    "adjclose": [{"adjclose": [100.0, 101.0]}],
                },
            }]
        }
    }
    monkeypatch.setattr(global_market_tracker.requests, "get", lambda *args, **kwargs: FakeResponse(payload))
    item = global_market_tracker.fetch_asset_snapshot("^GSPC")
    assert item["session_date"] == "2026-08-18"


def test_notifier_splits_long_morning_note_at_clean_section_boundary():
    first = "[2026년 8월 19일 모닝 시황]\n" + ("요약문장\n" * 260)
    second = "──────────\n<주요 테마>\n" + ("테마설명\n" * 260)
    text = first + second
    chunks = notifier._split_message(text, limit=1800)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 1800 for chunk in chunks)
    assert "".join(chunks) == text
    assert any(chunk.startswith("──────────\n<주요 테마>") for chunk in chunks[1:])
