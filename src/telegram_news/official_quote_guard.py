from __future__ import annotations

from typing import Any


_INSTALLED = False


def resolve_official_first(ticker: str, official_quote: Any, quote_type: Any) -> Any:
    """Prefer any usable exchange/Yahoo price before a derivative proxy.

    A closed market does not make the most recent official close invalid. A
    Hyperliquid same-symbol perp is consulted only when the current/extended and
    recent regular exchange prices are all unavailable. ``previous`` may be used
    solely as the divergence reference for that last-resort proxy.
    """
    from . import continuous_quote_fallback as cq

    if not cq._eligible_equity_ticker(ticker):
        return official_quote

    yahoo = cq._yahoo_extended_snapshot(ticker) or {}
    latest_price = cq._safe_float(yahoo.get("latest_price"))
    latest_epoch = yahoo.get("latest_epoch")
    regular_yahoo = cq._safe_float(yahoo.get("regular_price"))
    regular_epoch = yahoo.get("regular_epoch")
    previous = cq._safe_float(yahoo.get("previous"))
    official_price = cq._safe_float(getattr(official_quote, "price", None))

    latest_age = cq._age_minutes(latest_epoch)
    regular_age = cq._age_minutes(regular_epoch)
    latest_is_fresh = latest_price is not None and latest_age is not None and latest_age <= cq.FRESH_MAX_AGE_MINUTES
    regular_is_fresh = regular_yahoo is not None and regular_age is not None and regular_age <= cq.FRESH_MAX_AGE_MINUTES

    if latest_is_fresh and (not regular_is_fresh or not regular_epoch or int(latest_epoch) > int(regular_epoch) + 60):
        change_pct = ((latest_price - previous) / previous * 100.0) if previous else None
        return quote_type(
            ticker,
            latest_price,
            change_pct,
            getattr(official_quote, "turnover", None),
            "Yahoo Finance extended-hours",
            cq._now_kst(),
            None,
        )

    # The latest official close is a valid tradable-asset reference even when the
    # market is currently closed. Never replace it just because it is old.
    if official_price is not None:
        return official_quote

    if regular_yahoo is not None:
        change_pct = ((regular_yahoo - previous) / previous * 100.0) if previous else None
        return quote_type(
            ticker,
            regular_yahoo,
            change_pct,
            getattr(official_quote, "turnover", None),
            "Yahoo Finance recent regular close",
            cq._now_kst(),
            None,
        )

    # A stale extended bar is still an official Yahoo observation and is safer
    # than a crypto-venue derivative proxy.
    if latest_price is not None:
        change_pct = ((latest_price - previous) / previous * 100.0) if previous else None
        return quote_type(
            ticker,
            latest_price,
            change_pct,
            getattr(official_quote, "turnover", None),
            "Yahoo Finance recent observed price",
            cq._now_kst(),
            None,
        )

    # Only an actual official-price failure reaches this branch. The previous
    # official close is required so the proxy divergence guard can reject bad
    # mappings; without it, do not expose a proxy price at all.
    if previous is None or previous <= 0:
        return official_quote
    proxy = cq.fetch_hyperliquid_proxy(ticker, previous)
    if proxy is None:
        return official_quote
    change_pct = (proxy.price - previous) / previous * 100.0
    return quote_type(
        ticker,
        proxy.price,
        change_pct,
        None,
        proxy.source,
        proxy.timestamp,
        "24h derivative proxy; not official equity spot",
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import continuous_quote_fallback as cq

    current = cq._resolve_quote
    if getattr(current, "_official_quote_guard_installed", False):
        _INSTALLED = True
        return

    def wrapped(ticker: str, official_quote: Any, quote_type: Any) -> Any:
        return resolve_official_first(ticker, official_quote, quote_type)

    wrapped._official_quote_guard_installed = True
    wrapped._official_quote_guard_original = current
    cq._resolve_quote = wrapped
    _INSTALLED = True
    print("[official-quote-guard] exchange/official recent close required before derivative fallback")
