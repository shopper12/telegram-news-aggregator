from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import lru_cache
from urllib.parse import quote as urlquote
from zoneinfo import ZoneInfo
import math
import os

import requests

from . import market_data as base


GLOBAL_TICKERS = {
    "DOW": "^DJI",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
    "RUSSELL2000": "^RUT",
    "SOX": "^SOX",
    "DXY": "DX-Y.NYB",
    "VIX": "^VIX",
    "US10Y": "^TNX",
    "WTI": "CL=F",
}

KOREA_PROXIES = {
    "EWY": "EWY",
    "KORU": "KORU",
}

SECTOR_BASKETS = {
    "AI인프라": {"CoreWeave": "CRWV", "SuperMicro": "SMCI", "Nebius": "NBIS"},
    "반도체·메모리": {"NVIDIA": "NVDA", "Micron": "MU", "AMD": "AMD"},
    "광통신·네트워크": {"Lumentum": "LITE", "Coherent": "COHR", "Ciena": "CIEN"},
    "전력·데이터센터": {"Vertiv": "VRT", "BloomEnergy": "BE", "Eaton": "ETN"},
    "양자컴퓨팅": {"IonQ": "IONQ", "Rigetti": "RGTI"},
    "원자력": {"Oklo": "OKLO", "NanoNuclear": "NNE"},
    "배터리·리튬": {"Albemarle": "ALB"},
    "우주": {"RocketLab": "RKLB"},
}

WORKERS = max(2, min(12, int(os.getenv("MARKET_DASHBOARD_WORKERS", "8"))))


def _now_kst() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return None if math.isnan(out) or math.isinf(out) else out
    except Exception:
        return None


def _series(values) -> list[float]:
    return [number for value in (values or []) if (number := _safe_float(value)) is not None]


def _daily_change_pct(result: dict) -> float | None:
    indicators = result.get("indicators") or {}
    adjusted = ((indicators.get("adjclose") or [{}])[0]).get("adjclose") or []
    closes = ((indicators.get("quote") or [{}])[0]).get("close") or []
    series = _series(adjusted) or _series(closes)
    if len(series) < 2 or series[-2] == 0:
        return None
    return (series[-1] - series[-2]) / series[-2] * 100.0


def _session_date(result: dict, meta: dict) -> str | None:
    timestamps = [int(value) for value in (result.get("timestamp") or []) if value]
    epoch = timestamps[-1] if timestamps else meta.get("regularMarketTime")
    try:
        timezone_name = str(meta.get("exchangeTimezoneName") or "America/New_York")
        return datetime.fromtimestamp(int(epoch), ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        return None


@lru_cache(maxsize=128)
def _yahoo_quote(ticker: str) -> dict:
    # Daily bars are intentional. Yahoo's chartPreviousClose can refer to the
    # close before the whole requested range, so using it with range=5d/1mo can
    # silently turn a one-session change into a multi-day return.
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urlquote(ticker, safe='^=.-')}?range=1mo&interval=1d&events=div%2Csplits"
    )
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "telegram-news-aggregator/market-dashboard"},
            timeout=7,
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("chart", {}).get("result", [])[0]
        meta = result.get("meta", {})
        raw_closes = _series((((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or [])
        price = _safe_float(meta.get("regularMarketPrice")) or (raw_closes[-1] if raw_closes else None)
        change_pct = _daily_change_pct(result)
        if change_pct is None:
            previous = _safe_float(meta.get("previousClose"))
            change_pct = ((price - previous) / previous * 100) if price is not None and previous else None
        return {
            "ticker": ticker,
            "price": price,
            "change_pct": change_pct,
            "volume": _safe_float(meta.get("regularMarketVolume")),
            "session_date": _session_date(result, meta),
            "source": "Yahoo Finance daily bars",
            "timestamp": _now_kst(),
            "error": None if price is not None else "price_missing",
        }
    except Exception as exc:
        return {
            "ticker": ticker,
            "price": None,
            "change_pct": None,
            "volume": None,
            "session_date": None,
            "source": "Yahoo Finance daily bars",
            "timestamp": _now_kst(),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _fetch_named_quotes(mapping: dict[str, str]) -> list[dict]:
    ordered = list(mapping.items())
    output: dict[str, dict] = {}

    def work(label: str, ticker: str):
        item = dict(_yahoo_quote(ticker))
        item["label"] = label
        return label, item

    with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(ordered)))) as executor:
        futures = [executor.submit(work, label, ticker) for label, ticker in ordered]
        for future in as_completed(futures):
            label, item = future.result()
            output[label] = item
    return [output[label] for label, _ in ordered if label in output]


def _fetch_sector_baskets() -> dict[str, list[dict]]:
    jobs = [
        (sector, name, ticker)
        for sector, members in SECTOR_BASKETS.items()
        for name, ticker in members.items()
    ]
    grouped = {sector: [] for sector in SECTOR_BASKETS}

    def work(sector: str, name: str, ticker: str):
        item = dict(_yahoo_quote(ticker))
        item["label"] = name
        item["name"] = name
        return sector, item

    with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(jobs)))) as executor:
        futures = [executor.submit(work, *job) for job in jobs]
        for future in as_completed(futures):
            sector, item = future.result()
            grouped[sector].append(item)

    for sector in grouped:
        grouped[sector].sort(
            key=lambda item: (
                item.get("change_pct") is not None,
                float(item.get("change_pct") or -9999),
            ),
            reverse=True,
        )
    return grouped


def _find(quotes: list[dict], label: str) -> dict:
    return next((item for item in quotes if item.get("label") == label), {})


def _risk_regime(global_quotes: list[dict]) -> dict:
    score = 0
    reasons = []
    for label in ["S&P500", "NASDAQ", "RUSSELL2000", "SOX"]:
        change = _safe_float(_find(global_quotes, label).get("change_pct"))
        if change is None:
            continue
        score += 1 if change > 0 else -1 if change < 0 else 0
        reasons.append(f"{label} {change:+.2f}%")

    vix = _safe_float(_find(global_quotes, "VIX").get("change_pct"))
    if vix is not None:
        score += 1 if vix < 0 else -1 if vix > 0 else 0
        reasons.append(f"VIX {vix:+.2f}%")

    dxy = _safe_float(_find(global_quotes, "DXY").get("change_pct"))
    if dxy is not None:
        score += 1 if dxy < 0 else -1 if dxy > 0 else 0
        reasons.append(f"DXY {dxy:+.2f}%")

    regime = "RISK-ON" if score >= 4 else "RISK-OFF" if score <= -4 else "NEUTRAL"
    return {"regime": regime, "score": score, "reasons": reasons[:6]}


def _safe_base_context() -> dict:
    try:
        context = base.get_market_context()
        return context if isinstance(context, dict) else {}
    except Exception:
        return {}


def get_market_dashboard_context() -> dict:
    global_quotes = _fetch_named_quotes(GLOBAL_TICKERS)
    korea_proxies = _fetch_named_quotes(KOREA_PROXIES)
    sector_baskets = _fetch_sector_baskets()
    base_context = _safe_base_context()
    sp500 = _find(global_quotes, "S&P500")

    return {
        "global_market_quotes": global_quotes,
        "us_session_date": sp500.get("session_date"),
        "risk_regime": _risk_regime(global_quotes),
        "korea_proxies": korea_proxies,
        "sector_baskets": sector_baskets,
        "usd_krw": base_context.get("usd_krw"),
        "kospi_change_pct": base_context.get("kospi_change_pct"),
        "kosdaq_change_pct": base_context.get("kosdaq_change_pct"),
        "sp500_change_pct": base_context.get("sp500_change_pct"),
        "nasdaq_change_pct": base_context.get("nasdaq_change_pct"),
        "investor_flow": base_context.get("investor_flow") or [],
        "supply_demand_line": base_context.get("supply_demand_line") or "투자자별 수급 확인불가",
        "market_bias": base_context.get("market_bias") or "시장 판단 미확인",
        "top_sectors_by_volume": base_context.get("top_sectors_by_volume") or [],
        "market_cap_leaders": base_context.get("market_cap_leaders") or [],
        "source": "Yahoo Finance daily bars + existing pykrx/Naver/Yahoo market context",
        "timestamp": _now_kst(),
    }
