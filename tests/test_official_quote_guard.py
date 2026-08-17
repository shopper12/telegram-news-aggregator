from __future__ import annotations

from datetime import datetime, timezone

from telegram_news import continuous_quote_fallback as cq
from telegram_news import official_quote_guard as guard
from telegram_news.market_data import Quote


def _epoch(minutes_ago: int) -> int:
    return int(datetime.now(timezone.utc).timestamp() - minutes_ago * 60)


def test_stale_but_valid_official_close_never_uses_hyperliquid(monkeypatch):
    official = Quote("EWY", 100.0, 1.0, 123.0, "Yahoo Finance", "old")
    monkeypatch.setattr(
        cq,
        "_yahoo_extended_snapshot",
        lambda ticker: {
            "latest_price": 100.0,
            "latest_epoch": _epoch(900),
            "regular_price": 100.0,
            "regular_epoch": _epoch(900),
            "previous": 99.0,
        },
    )
    monkeypatch.setattr(
        cq,
        "fetch_hyperliquid_proxy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("proxy must not be queried while an official close exists")),
    )

    quote = guard.resolve_official_first("EWY", official, Quote)

    assert quote is official
    assert quote.price == 100.0
    assert quote.source == "Yahoo Finance"


def test_recent_yahoo_regular_close_precedes_proxy_when_primary_quote_failed(monkeypatch):
    official = Quote("SOXX", None, None, None, "Yahoo Finance", "failed", "price_missing")
    monkeypatch.setattr(
        cq,
        "_yahoo_extended_snapshot",
        lambda ticker: {
            "latest_price": None,
            "latest_epoch": None,
            "regular_price": 300.0,
            "regular_epoch": _epoch(800),
            "previous": 298.0,
        },
    )
    monkeypatch.setattr(
        cq,
        "fetch_hyperliquid_proxy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("proxy must not be queried while Yahoo has a recent regular close")),
    )

    quote = guard.resolve_official_first("SOXX", official, Quote)

    assert quote.price == 300.0
    assert quote.source == "Yahoo Finance recent regular close"


def test_proxy_is_last_resort_only_after_official_price_failure(monkeypatch):
    official = Quote("EWY", None, None, None, "Yahoo Finance", "failed", "price_missing")
    monkeypatch.setattr(
        cq,
        "_yahoo_extended_snapshot",
        lambda ticker: {
            "latest_price": None,
            "latest_epoch": None,
            "regular_price": None,
            "regular_epoch": None,
            "previous": 100.0,
        },
    )
    calls = []

    def proxy(ticker, reference_price):
        calls.append((ticker, reference_price))
        return cq.ProxyPrice(
            ticker=ticker,
            price=101.0,
            dex="xyz",
            coin=ticker,
            source=f"Hyperliquid HIP-3 xyz:{ticker} 24h perp proxy",
            timestamp="now",
        )

    monkeypatch.setattr(cq, "fetch_hyperliquid_proxy", proxy)

    quote = guard.resolve_official_first("EWY", official, Quote)

    assert calls == [("EWY", 100.0)]
    assert quote.price == 101.0
    assert "Hyperliquid HIP-3" in quote.source
    assert quote.error == "24h derivative proxy; not official equity spot"


def test_proxy_is_not_used_without_official_reference_for_divergence_check(monkeypatch):
    official = Quote("GLD", None, None, None, "Yahoo Finance", "failed", "price_missing")
    monkeypatch.setattr(cq, "_yahoo_extended_snapshot", lambda ticker: {})
    monkeypatch.setattr(
        cq,
        "fetch_hyperliquid_proxy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unbounded proxy must not be queried")),
    )

    quote = guard.resolve_official_first("GLD", official, Quote)

    assert quote is official
    assert quote.price is None
