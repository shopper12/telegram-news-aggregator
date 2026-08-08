from __future__ import annotations

from datetime import datetime, timezone

from telegram_news import continuous_quote_fallback as cq
from telegram_news.market_data import Quote


def _epoch(minutes_ago: int) -> int:
    return int(datetime.now(timezone.utc).timestamp() - minutes_ago * 60)


def test_extended_hours_yahoo_is_preferred_before_derivative_proxy(monkeypatch):
    official = Quote("NVDA", 100.0, 1.0, 123.0, "Yahoo Finance", "now")
    monkeypatch.setattr(
        cq,
        "_yahoo_extended_snapshot",
        lambda ticker: {
            "latest_price": 103.0,
            "latest_epoch": _epoch(5),
            "regular_price": 100.0,
            "regular_epoch": _epoch(240),
            "previous": 98.0,
        },
    )
    monkeypatch.setattr(
        cq,
        "fetch_hyperliquid_proxy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Hyperliquid should not be called")),
    )

    quote = cq._resolve_quote("NVDA", official, Quote)

    assert quote.price == 103.0
    assert quote.source == "Yahoo Finance extended-hours"
    assert quote.change_pct == (103.0 - 98.0) / 98.0 * 100.0


def test_stale_yahoo_uses_hyperliquid_hip3_proxy(monkeypatch):
    official = Quote("NVDA", 100.0, 2.0, 123.0, "Yahoo Finance", "now")
    monkeypatch.setattr(
        cq,
        "_yahoo_extended_snapshot",
        lambda ticker: {
            "latest_price": 100.0,
            "latest_epoch": _epoch(600),
            "regular_price": 100.0,
            "regular_epoch": _epoch(600),
            "previous": 98.0,
        },
    )
    monkeypatch.setattr(
        cq,
        "fetch_hyperliquid_proxy",
        lambda ticker, reference_price: cq.ProxyPrice(
            ticker="NVDA",
            price=102.5,
            dex="xyz",
            coin="NVDA",
            source="Hyperliquid HIP-3 xyz:NVDA 24h perp proxy",
            timestamp="2026-08-08 12:30 KST",
        ),
    )

    quote = cq._resolve_quote("NVDA", official, Quote)

    assert quote.price == 102.5
    assert quote.source == "Hyperliquid HIP-3 xyz:NVDA 24h perp proxy"
    assert quote.error == "24h derivative proxy; not official equity spot"
    assert quote.change_pct == (102.5 - 98.0) / 98.0 * 100.0


def test_hyperliquid_proxy_requires_same_symbol_and_sane_divergence(monkeypatch):
    monkeypatch.setattr(
        cq,
        "_refresh_hyperliquid_markets",
        lambda: {
            "NVDA": cq.ProxyPrice(
                ticker="NVDA",
                price=145.0,
                dex="xyz",
                coin="xyz:NVDA",
                source="Hyperliquid HIP-3 xyz:NVDA 24h perp proxy",
                timestamp="now",
            ),
            "TSLA": cq.ProxyPrice(
                ticker="TSLA",
                price=250.0,
                dex="xyz",
                coin="TSLA",
                source="Hyperliquid HIP-3 xyz:TSLA 24h perp proxy",
                timestamp="now",
            ),
        },
    )
    monkeypatch.setattr(cq, "MAX_PROXY_DIVERGENCE_PCT", 30.0)

    # 45% gap versus official spot is rejected as a likely bad/stale mapping.
    assert cq.fetch_hyperliquid_proxy("NVDA", 100.0) is None
    # Exact same-symbol match inside the sanity band is accepted.
    proxy = cq.fetch_hyperliquid_proxy("TSLA", 240.0)
    assert proxy is not None
    assert proxy.price == 250.0
    # Indexes/FX/Korean stocks are never guessed onto a Hyperliquid symbol.
    assert cq.fetch_hyperliquid_proxy("^GSPC", 6000.0) is None
    assert cq.fetch_hyperliquid_proxy("005930.KS", 80000.0) is None


def test_hyperliquid_market_discovery_scans_builder_dexes(monkeypatch):
    calls = []

    def fake_post(payload):
        calls.append(payload)
        if payload == {"type": "perpDexs"}:
            return [None, {"name": "crypto"}, {"name": "xyz"}]
        if payload == {"type": "allMids", "dex": "xyz"}:
            return {"xyz:NVDA": "101.25"}
        if payload == {"type": "allMids", "dex": "crypto"}:
            return {"BTC": "100000"}
        raise AssertionError(payload)

    monkeypatch.setattr(cq, "_post_hyperliquid", fake_post)
    monkeypatch.setattr(cq, "_HL_MARKETS", {})
    monkeypatch.setattr(cq, "_HL_CACHE_AT", 0.0)
    monkeypatch.delenv("HYPERLIQUID_PROXY_DEXS", raising=False)

    markets = cq._refresh_hyperliquid_markets()

    assert markets["NVDA"].price == 101.25
    assert markets["NVDA"].dex == "xyz"
    assert {"type": "perpDexs"} in calls
    assert {"type": "allMids", "dex": "xyz"} in calls
