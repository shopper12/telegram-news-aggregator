from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
import re
from time import monotonic
from typing import Any
from urllib.parse import quote as urlquote
from zoneinfo import ZoneInfo

import requests


KST = ZoneInfo("Asia/Seoul")
YAHOO_TIMEOUT = float(os.getenv("CONTINUOUS_QUOTE_YAHOO_TIMEOUT", "5.0"))
HYPERLIQUID_TIMEOUT = float(os.getenv("HYPERLIQUID_INFO_TIMEOUT", "5.0"))
FRESH_MAX_AGE_MINUTES = float(os.getenv("CONTINUOUS_QUOTE_MAX_AGE_MINUTES", "120"))
MAX_PROXY_DIVERGENCE_PCT = float(os.getenv("HYPERLIQUID_PROXY_MAX_DIVERGENCE_PCT", "30"))
HYPERLIQUID_MAX_DEXS = int(os.getenv("HYPERLIQUID_PROXY_MAX_DEXS", "32"))
HYPERLIQUID_WORKERS = int(os.getenv("HYPERLIQUID_PROXY_WORKERS", "8"))
HYPERLIQUID_CACHE_SECONDS = float(os.getenv("HYPERLIQUID_PROXY_CACHE_SECONDS", "90"))
HYPERLIQUID_INFO_URL = os.getenv("HYPERLIQUID_INFO_URL", "https://api.hyperliquid.xyz/info")

_INSTALLED = False
_HL_CACHE_AT = 0.0
_HL_MARKETS: dict[str, "ProxyPrice"] = {}


@dataclass(frozen=True)
class ProxyPrice:
    ticker: str
    price: float
    dex: str
    coin: str
    source: str
    timestamp: str


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return None if math.isnan(out) or math.isinf(out) else out
    except Exception:
        return None


def _eligible_equity_ticker(ticker: str) -> bool:
    ticker = str(ticker or "").strip().upper()
    if not ticker or ticker.startswith("^") or ticker.endswith((".KS", ".KQ", "=X", "-USD")):
        return False
    if "=" in ticker or "/" in ticker:
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", ticker))


def _symbol_key(value: str) -> str:
    text = str(value or "").upper().strip()
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    for suffix in ("-PERP", "/USD", "-USD"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return re.sub(r"[^A-Z0-9]", "", text)


def _post_hyperliquid(payload: dict[str, Any]) -> Any:
    try:
        response = requests.post(
            HYPERLIQUID_INFO_URL,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "telegram-news-aggregator/continuous-quotes"},
            timeout=HYPERLIQUID_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _configured_dex_names() -> list[str]:
    explicit = [item.strip() for item in os.getenv("HYPERLIQUID_PROXY_DEXS", "").split(",") if item.strip()]
    if explicit:
        return explicit[:HYPERLIQUID_MAX_DEXS]

    payload = _post_hyperliquid({"type": "perpDexs"})
    names: list[str] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)

    # Equity-oriented builder DEXes are checked first; remaining HIP-3 DEXes are
    # still scanned so the code does not depend on one operator or hard-coded venue.
    hints = ("stock", "equity", "cash", "trade", "xyz", "rwa")
    names.sort(key=lambda name: (0 if any(hint in name.lower() for hint in hints) else 1, name.lower()))
    return names[:HYPERLIQUID_MAX_DEXS]


def _refresh_hyperliquid_markets() -> dict[str, ProxyPrice]:
    global _HL_CACHE_AT, _HL_MARKETS
    now = monotonic()
    if _HL_MARKETS and now - _HL_CACHE_AT < HYPERLIQUID_CACHE_SECONDS:
        return _HL_MARKETS

    dex_names = _configured_dex_names()
    mids_by_dex: dict[str, Any] = {}
    if dex_names:
        workers = max(1, min(HYPERLIQUID_WORKERS, len(dex_names)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hyperliquid-proxy") as pool:
            futures = {
                pool.submit(_post_hyperliquid, {"type": "allMids", "dex": dex}): dex
                for dex in dex_names
            }
            for future in as_completed(futures):
                dex = futures[future]
                try:
                    mids_by_dex[dex] = future.result()
                except Exception:
                    mids_by_dex[dex] = None

    markets: dict[str, ProxyPrice] = {}
    fetched_at = _now_kst()
    # Iterate in priority order even though HTTP requests completed concurrently.
    for dex in dex_names:
        mids = mids_by_dex.get(dex)
        if not isinstance(mids, dict):
            continue
        for coin, raw_price in mids.items():
            price = _safe_float(raw_price)
            key = _symbol_key(str(coin))
            if not key or price is None or price <= 0:
                continue
            markets.setdefault(
                key,
                ProxyPrice(
                    ticker=key,
                    price=price,
                    dex=dex,
                    coin=str(coin),
                    source=f"Hyperliquid HIP-3 {dex}:{coin} 24h perp proxy",
                    timestamp=fetched_at,
                ),
            )

    _HL_MARKETS = markets
    _HL_CACHE_AT = now
    return markets


def fetch_hyperliquid_proxy(ticker: str, reference_price: float | None) -> ProxyPrice | None:
    """Return a same-symbol HIP-3 perp mid only when it is sane versus official spot.

    The price is a derivative proxy, never an official equity spot print. Requiring
    a recent/last official reference price prevents accidental matches to unrelated
    crypto markets with the same symbol.
    """
    if not _eligible_equity_ticker(ticker):
        return None
    reference = _safe_float(reference_price)
    if reference is None or reference <= 0:
        return None

    candidate = _refresh_hyperliquid_markets().get(_symbol_key(ticker))
    if candidate is None:
        return None
    divergence = abs(candidate.price / reference - 1.0) * 100.0
    if divergence > MAX_PROXY_DIVERGENCE_PCT:
        return None
    return candidate


def _yahoo_extended_snapshot(ticker: str) -> dict[str, Any] | None:
    encoded = urlquote(ticker.replace(".", "-"), safe="^-=")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        "?range=5d&interval=5m&includePrePost=true"
    )
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "telegram-news-aggregator/continuous-quotes"},
            timeout=YAHOO_TIMEOUT,
        )
        response.raise_for_status()
        result = (response.json().get("chart", {}).get("result") or [None])[0]
        if not isinstance(result, dict):
            return None
    except Exception:
        return None

    meta = result.get("meta") or {}
    timestamps = list(result.get("timestamp") or [])
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = list(quotes.get("close") or [])
    latest_price = None
    latest_epoch = None
    for epoch, close in reversed(list(zip(timestamps, closes))):
        value = _safe_float(close)
        if value is not None and epoch:
            latest_price = value
            latest_epoch = int(epoch)
            break

    regular_price = _safe_float(meta.get("regularMarketPrice"))
    regular_epoch = meta.get("regularMarketTime")
    try:
        regular_epoch = int(regular_epoch) if regular_epoch else None
    except Exception:
        regular_epoch = None

    previous = _safe_float(meta.get("chartPreviousClose") or meta.get("previousClose"))
    return {
        "latest_price": latest_price,
        "latest_epoch": latest_epoch,
        "regular_price": regular_price,
        "regular_epoch": regular_epoch,
        "previous": previous,
    }


def _age_minutes(epoch: int | None) -> float | None:
    if not epoch:
        return None
    return max(0.0, (datetime.now(timezone.utc).timestamp() - float(epoch)) / 60.0)


def _resolve_quote(ticker: str, official_quote: Any, quote_type: Any) -> Any:
    if not _eligible_equity_ticker(ticker):
        return official_quote

    yahoo = _yahoo_extended_snapshot(ticker) or {}
    latest_price = _safe_float(yahoo.get("latest_price"))
    latest_epoch = yahoo.get("latest_epoch")
    regular_price = _safe_float(yahoo.get("regular_price")) or _safe_float(getattr(official_quote, "price", None))
    regular_epoch = yahoo.get("regular_epoch")
    previous = _safe_float(yahoo.get("previous"))

    latest_age = _age_minutes(latest_epoch)
    regular_age = _age_minutes(regular_epoch)
    latest_is_fresh = latest_price is not None and latest_age is not None and latest_age <= FRESH_MAX_AGE_MINUTES
    regular_is_fresh = regular_price is not None and regular_age is not None and regular_age <= FRESH_MAX_AGE_MINUTES

    # During pre-market/after-hours Yahoo's latest 5m bar can be newer than the
    # regular session print. Prefer that official extended-hours feed first.
    if latest_is_fresh and (not regular_is_fresh or not regular_epoch or int(latest_epoch) > int(regular_epoch) + 60):
        change_pct = ((latest_price - previous) / previous * 100.0) if previous else None
        return quote_type(
            ticker,
            latest_price,
            change_pct,
            getattr(official_quote, "turnover", None),
            "Yahoo Finance extended-hours",
            _now_kst(),
            None,
        )

    # If the official regular quote is fresh, keep the original spot source.
    if regular_is_fresh:
        return official_quote

    # Weekend/holiday/overnight: try a same-symbol 24h HIP-3 perpetual as a
    # verification proxy. It is deliberately labelled as a derivative.
    reference = regular_price or latest_price or _safe_float(getattr(official_quote, "price", None))
    proxy = fetch_hyperliquid_proxy(ticker, reference)
    if proxy is None:
        return official_quote

    change_pct = ((proxy.price - previous) / previous * 100.0) if previous else None
    return quote_type(
        ticker,
        proxy.price,
        change_pct,
        None,
        proxy.source,
        proxy.timestamp,
        "24h derivative proxy; not official equity spot",
    )


def _resolve_snapshot_item(ticker: str, item: dict[str, Any], quote_type: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or not _eligible_equity_ticker(ticker):
        return item
    official = quote_type(
        ticker,
        _safe_float(item.get("price")),
        _safe_float(item.get("change_pct")),
        None,
        "Yahoo Finance regular-session snapshot",
        str(item.get("timestamp") or _now_kst()),
        str(item.get("error")) if item.get("error") else None,
    )
    resolved = _resolve_quote(ticker, official, quote_type)
    out = dict(item)
    out["price_source"] = str(getattr(resolved, "source", "") or official.source)
    out["price_timestamp"] = str(getattr(resolved, "timestamp", "") or official.timestamp)
    warning = str(getattr(resolved, "error", "") or "")
    if warning:
        out["price_warning"] = warning
    if getattr(resolved, "price", None) is not None:
        out["price"] = float(resolved.price)
    if getattr(resolved, "change_pct", None) is not None:
        out["change_pct"] = float(resolved.change_pct)
    out["price_is_derivative_proxy"] = "Hyperliquid HIP-3" in out["price_source"]
    return out


def _install_global_snapshot_fallback(market_data: Any) -> None:
    from . import global_market_tracker

    current = global_market_tracker.fetch_asset_snapshot
    if getattr(current, "_continuous_quote_fallback_installed", False):
        return
    original = current

    def wrapped(ticker: str):
        return _resolve_snapshot_item(ticker, original(ticker), market_data.Quote)

    wrapped._continuous_quote_fallback_installed = True
    wrapped._continuous_quote_fallback_original = original
    global_market_tracker.fetch_asset_snapshot = wrapped


def _install_adaptive_price_provenance() -> None:
    from . import adaptive_strategy

    current_candidates = adaptive_strategy._candidates
    if not getattr(current_candidates, "_continuous_quote_source_installed", False):
        original_candidates = current_candidates

        def wrapped_candidates(snapshot, memory, state, now):
            candidates = original_candidates(snapshot, memory, state, now)
            assets = snapshot.get("assets") or {}
            for candidate in candidates:
                item = assets.get(candidate.get("ticker")) or {}
                candidate["price_source"] = str(item.get("price_source") or "Yahoo Finance regular-session snapshot")
                candidate["price_warning"] = str(item.get("price_warning") or "")
            return candidates

        wrapped_candidates._continuous_quote_source_installed = True
        wrapped_candidates._continuous_quote_source_original = original_candidates
        adaptive_strategy._candidates = wrapped_candidates

    current_recommendation = adaptive_strategy._recommendation
    if not getattr(current_recommendation, "_continuous_quote_source_installed", False):
        original_recommendation = current_recommendation

        def wrapped_recommendation(candidate, slot, state, now):
            recommendation = original_recommendation(candidate, slot, state, now)
            source = str(candidate.get("price_source") or "")
            warning = str(candidate.get("price_warning") or "")
            if source:
                recommendation["price_source"] = source
                if "Hyperliquid HIP-3" in source:
                    recommendation["reason"] += f" / 가격소스: {source} (24h 파생 프록시·현물 아님)"
                elif "extended-hours" in source:
                    recommendation["reason"] += f" / 가격소스: {source}"
            if warning:
                recommendation["price_warning"] = warning
            return recommendation

        wrapped_recommendation._continuous_quote_source_installed = True
        wrapped_recommendation._continuous_quote_source_original = original_recommendation
        adaptive_strategy._recommendation = wrapped_recommendation


def install() -> None:
    """Install the fallback into canonical single-stock and adaptive strategy paths."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import market_data

    current = market_data._fetch_us_quote
    if not getattr(current, "_continuous_quote_fallback_installed", False):
        original = current

        def wrapped(ticker: str):
            official = original(ticker)
            return _resolve_quote(ticker, official, market_data.Quote)

        wrapped._continuous_quote_fallback_installed = True
        wrapped._continuous_quote_fallback_original = original
        market_data._fetch_us_quote = wrapped
        try:
            market_data.fetch_quote.cache_clear()
        except Exception:
            pass

    _install_global_snapshot_fallback(market_data)
    _install_adaptive_price_provenance()
    _INSTALLED = True
    print("[continuous-quotes] Yahoo extended-hours + Hyperliquid HIP-3 fallback installed")


def install_messenger_quote_fallback(api_module: Any) -> None:
    """Use the same continuous quote path for MessengerBotR `봇 시세` replies."""
    current = getattr(api_module, "_quote", None)
    if not callable(current) or getattr(current, "_continuous_quote_fallback_installed", False):
        return

    original = current

    def wrapped(body: str) -> str:
        target = re.sub(r"^(시세|quote)\s*", "", str(body or ""), flags=re.IGNORECASE).strip()
        if not target:
            return original(body)
        try:
            symbol = api_module._quote_symbol(target)
            if _eligible_equity_ticker(symbol):
                from . import market_data

                quote = market_data._fetch_us_quote(symbol)
                source = str(getattr(quote, "source", "") or "")
                if quote.price is not None and ("extended-hours" in source or "Hyperliquid HIP-3" in source):
                    proxy_notice = "\n주의: 파생상품 프록시이며 실제 주식 현물 체결가가 아닙니다." if "Hyperliquid HIP-3" in source else ""
                    change = f" ({quote.change_pct:+.2f}%)" if quote.change_pct is not None else ""
                    return (
                        f"대체 시세: {target}\n"
                        f"{symbol}: {quote.price:,.4f}{change}\n"
                        f"소스: {source} / {quote.timestamp}"
                        f"{proxy_notice}"
                    )
        except Exception:
            pass
        return original(body)

    wrapped._continuous_quote_fallback_installed = True
    wrapped._continuous_quote_fallback_original = original
    api_module._quote = wrapped
