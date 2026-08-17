from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from telegram_news import consistency_rules as rules
from telegram_news import continuous_quote_fallback as cq
from telegram_news import market_note_formatter as formatter
from telegram_news import report_integrity as integrity
from telegram_news.strategy_learning import default_state


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 17, 7, 0, tzinfo=KST)


def _global_snapshot(sp500=-0.17):
    return {
        "regime": "mixed",
        "regime_label": "혼조",
        "regime_score": 0.0,
        "flow_proxy": "성장주 -0.10% / 신흥국 +0.10%",
        "assets": {
            "^DJI": {"price": 45000.0, "change_pct": -0.21},
            "^GSPC": {"price": 6800.0, "change_pct": sp500},
            "^IXIC": {"price": 23000.0, "change_pct": -0.08},
            "^RUT": {"price": 2400.0, "change_pct": 0.12},
            "^SOX": {"price": 6500.0, "change_pct": -0.33},
            "^TNX": {"price": 4.20, "change_pct": 0.10},
            "^TYX": {"price": 4.90, "change_pct": 0.10},
            "^VIX": {"price": 17.0, "change_pct": 1.0},
            "CL=F": {"price": 80.0, "change_pct": -0.2},
            "QQQ": {"price": 600.0, "change_pct": -0.10},
            "EEM": {"price": 50.0, "change_pct": 0.10},
            "HYG": {"price": 80.0, "change_pct": 0.00},
            "GLD": {"price": 250.0, "change_pct": 0.20},
            "TLT": {"price": 90.0, "change_pct": -0.10},
        },
    }


def _market_snapshot(kind="strategy_morning"):
    return integrity.build_market_snapshot(
        kind=kind,
        market_context={
            "kospi_change_pct": 0.91,
            "kosdaq_change_pct": 8.24,
            "sp500_change_pct": 0.36,
            "nasdaq_change_pct": 0.14,
            "usd_krw": 1414.1,
        },
        global_snapshot=_global_snapshot(),
        captured_at=NOW,
    )


def _context(*, displayed=()):
    return integrity.ReportRunContext(
        market=_market_snapshot(),
        news=integrity.NewsPool(
            summaries=(),
            selected=(),
            displayed=tuple(displayed),
            suppressed_count=0,
            stock_count=0,
            blocked_count=0,
            pre_gate_count=0,
            rule="test",
        ),
        hours=6,
        timezone_name="Asia/Seoul",
    )


def test_us_closing_header_and_axis_use_exact_same_sp500_snapshot(monkeypatch):
    snapshot = _market_snapshot("strategy_morning")
    context = integrity.ReportRunContext(
        market=snapshot,
        news=integrity.NewsPool((), (), (), 0, 0, 0, 0, "test"),
        hours=6,
        timezone_name="Asia/Seoul",
    )
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda value: value)
    monkeypatch.setattr(formatter, "_index_lines", integrity.snapshot_index_lines)
    monkeypatch.setattr(rules, "_index_axis", integrity.atomic_index_axis)

    with integrity.activate_context(context):
        note = formatter.build_market_note(
            original_report="검증: atomic-test",
            summaries=[],
            hours=6,
            timezone_name="Asia/Seoul",
            kind="strategy_morning",
            now=NOW,
            market_context=snapshot.market_context,
            snapshot=snapshot.global_snapshot,
            selected=[],
        )

    header_match = __import__("re").search(r"S&P500 [▲▼―] ([+-]\d+\.\d+)%", note)
    axis_match = __import__("re").search(r"축1 지수\(30%\).*?S&P500 ([+-]\d+\.\d+)%", note)
    assert header_match and axis_match
    assert float(header_match.group(1)) == -0.17
    assert float(axis_match.group(1)) == -0.17
    assert header_match.group(1) == axis_match.group(1)
    assert "S&P500 +0.36%" not in note
    assert "KOSDAQ +8.24%→" not in note


def test_us_closing_index_axis_rejects_kosdaq_field():
    snapshot = _market_snapshot("strategy_morning")
    with pytest.raises(ValueError, match="not allowed"):
        integrity.atomic_index_axis(
            snapshot.market_context,
            market_snapshot=snapshot,
            requested_fields=["sp500_change_pct", "kosdaq_change_pct"],
        )


def test_market_snapshot_is_frozen_and_context_reuses_same_global_object():
    snapshot = _market_snapshot("strategy_morning")
    context = integrity.ReportRunContext(
        market=snapshot,
        news=integrity.NewsPool((), (), (), 0, 0, 0, 0, "test"),
        hours=6,
        timezone_name="Asia/Seoul",
    )
    with integrity.activate_context(context):
        assert integrity._context_global_snapshot() is snapshot.global_snapshot
        assert integrity._context_market_context() is snapshot.market_context
    with pytest.raises(Exception):
        snapshot.report_kind = "intraday"


def test_shared_news_pool_prevents_second_selector_call(monkeypatch):
    selected_marker = SimpleNamespace(name="same-selected-object")
    pool = integrity.NewsPool(
        summaries=(SimpleNamespace(title="one"),),
        selected=(selected_marker,),
        displayed=(selected_marker,),
        suppressed_count=0,
        stock_count=1,
        blocked_count=0,
        pre_gate_count=1,
        rule="shared",
    )
    context = integrity.ReportRunContext(_market_snapshot(), pool, 1, "Asia/Seoul")
    monkeypatch.setitem(
        integrity._ORIGINALS,
        "select_strict",
        lambda items: (_ for _ in ()).throw(AssertionError("selector must not be re-run")),
    )
    with integrity.activate_context(context):
        selected, stock_count, blocked, rule, pre_gate = integrity._context_select_strict([])
    assert selected == [selected_marker]
    assert (stock_count, blocked, rule, pre_gate) == (1, 0, "shared", 1)


def test_zero_new_strategy_memory_label_and_clickable_source_url():
    recommendation = {
        "asset": "미국 반도체",
        "ticker": "SOXX",
        "score": 3.2,
        "entry_zone": [100.0, 101.0],
        "entry_price": 100.5,
        "stop_price": 96.0,
        "target_price": 109.0,
        "component_text": "momentum +1.00, regime +1.00, news +1.20, defensive +0.00",
        "reason": "연결 뉴스 사용",
        "news_evidence": [
            {
                "title": "마이크론 HBM 공급 확대",
                "urls": ["https://example.com/micron-original"],
                "last_seen": NOW.isoformat(),
            }
        ],
        "price_is_derivative_proxy": False,
    }
    state = default_state()
    with integrity.activate_context(_context(displayed=())):
        section = integrity._build_strategy_section(
            _global_snapshot(),
            {"events": []},
            state,
            {"recommendations": []},
            "morning",
            [recommendation],
            [],
            0,
        )
    assert "상단 신규 이슈(0건)와는 별개 데이터" in section
    assert "최근 24시간 누적 메모리 기준" in section
    assert "마이크론 HBM 공급 확대" in section
    assert "https://example.com/micron-original" in section


def test_unverifiable_memory_news_cannot_influence_news_score():
    meta = {"keywords": ["hbm"]}
    memory = {
        "events": [
            {
                "title": "링크 없는 HBM 루머",
                "keywords": ["HBM"],
                "sectors": ["반도체"],
                "tickers": ["MU"],
                "source_urls": [],
                "materiality": 100,
                "sentiment": 3,
                "last_seen": NOW.isoformat(),
                "count": 1,
            }
        ]
    }
    score, evidence = integrity._news_score_with_evidence(meta, memory, NOW)
    assert score == 0.0
    assert evidence == []


def test_proxy_prices_are_visibly_marked_in_every_strategy_price():
    recommendation = {
        "asset": "한국 주식",
        "ticker": "EWY",
        "score": 2.5,
        "entry_zone": [199.0, 200.0],
        "entry_price": 199.5,
        "stop_price": 194.0,
        "target_price": 210.0,
        "component_text": "momentum +1.00, regime +1.00, news +0.50, defensive +0.00",
        "reason": "가격소스: 파생 프록시",
        "news_evidence": [],
        "price_is_derivative_proxy": True,
        "proxy_divergence_pct": 1.25,
    }
    section = integrity._build_strategy_section(
        _global_snapshot(),
        {"events": []},
        default_state(),
        {"recommendations": []},
        "morning",
        [recommendation],
        [],
        0,
    )
    assert section.count("(프록시, 실거래가 아님)") >= 5
    assert "실제가 대비 괴리 1.25%" in section


def test_hyperliquid_proxy_above_two_percent_is_rejected_and_logged(monkeypatch, capsys):
    proxy = cq.ProxyPrice(
        ticker="EWY",
        price=103.0,
        dex="xyz",
        coin="EWY",
        source="Hyperliquid HIP-3 xyz:EWY 24h perp proxy",
        timestamp="now",
    )
    monkeypatch.setattr(cq, "_refresh_hyperliquid_markets", lambda: {"EWY": proxy})
    monkeypatch.setattr(integrity, "PROXY_MAX_DIVERGENCE_PCT", 2.0)
    integrity._PROXY_CHECKS.clear()

    result = integrity._safe_fetch_hyperliquid_proxy("EWY", 100.0)

    assert result is None
    assert integrity._PROXY_CHECKS["EWY"]["accepted"] is False
    assert integrity._PROXY_CHECKS["EWY"]["divergence_pct"] == pytest.approx(3.0)
    output = capsys.readouterr().out
    assert "divergence=3.000%" in output
    assert "accepted=false" in output


def test_candidate_with_excessive_proxy_divergence_is_excluded():
    snapshot = _global_snapshot()
    snapshot["assets"]["EWY"] = {
        "price": 100.0,
        "change_pct": 1.0,
        "return_5d": 2.0,
        "return_20d": 3.0,
        "volatility_20d": 1.0,
        "proxy_divergence_exceeded": True,
        "proxy_divergence_pct": 2.5,
    }
    candidates = integrity._candidates_with_provenance(snapshot, {"events": []}, default_state(), NOW)
    assert all(item["ticker"] != "EWY" for item in candidates)
