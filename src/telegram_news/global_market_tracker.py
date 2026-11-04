from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import math
import os
from typing import Any
from zoneinfo import ZoneInfo

import requests


KST = ZoneInfo("Asia/Seoul")
REQUEST_TIMEOUT = float(os.getenv("GLOBAL_MARKET_REQUEST_TIMEOUT", "6.0"))

TRADE_ASSETS: dict[str, dict[str, Any]] = {
    "QQQ": {"name": "나스닥100", "group": "risk", "keywords": ["ai", "빅테크", "기술", "소프트웨어", "나스닥"]},
    "SOXX": {"name": "미국 반도체", "group": "risk", "keywords": ["반도체", "hbm", "ai인프라", "gpu", "메모리"]},
    "SPY": {"name": "S&P500", "group": "risk", "keywords": ["미국 증시", "s&p500", "경기", "실적"]},
    "IWM": {"name": "미국 중소형주", "group": "risk", "keywords": ["중소형", "금리 인하", "내수", "러셀"]},
    "EWY": {"name": "한국 주식", "group": "risk", "keywords": ["한국", "코스피", "코스닥", "반도체", "자동차", "조선", "방산"]},
    "EEM": {"name": "신흥국", "group": "risk", "keywords": ["중국", "신흥국", "아시아", "위안화"]},
    "GLD": {"name": "금", "group": "defensive", "keywords": ["금", "전쟁", "지정학", "인플레이션", "안전자산"]},
    "TLT": {"name": "미국 장기국채", "group": "defensive", "keywords": ["금리 인하", "침체", "국채", "연준 완화"]},
    "UUP": {"name": "달러", "group": "defensive", "keywords": ["달러 강세", "위험회피", "환율 급등"]},
    "USO": {"name": "원유", "group": "commodity", "keywords": ["유가", "원유", "opec", "중동", "공급 차질"]},
    "BTC-USD": {"name": "비트코인", "group": "risk", "keywords": ["비트코인", "암호자산", "가상자산"]},
}

GLOBAL_PROXIES = {
    "HYG": "하이일드",
    "^VIX": "VIX",
    "^TNX": "미10년금리",
    "DX-Y.NYB": "달러인덱스",
    "^GSPC": "S&P500",
    "^IXIC": "나스닥",
    "^RUT": "러셀2000",
    "^SOX": "필라델피아 반도체",
    "^N225": "일본",
    "^HSI": "홍콩",
    "000001.SS": "중국",
    "^STOXX50E": "유럽",
}
RISK_TICKERS = {"QQQ", "SOXX", "SPY", "IWM", "EWY", "EEM", "HYG", "BTC-USD"}
US_VALIDATION_TICKERS = RISK_TICKERS | {"GLD", "TLT", "UUP", "USO", "^VIX", "^TNX"}
BENCHMARK_PAIRS: dict[str, tuple[str, float]] = {
    "SPY": ("^GSPC", 0.75),
    "QQQ": ("^IXIC", 3.0),
    "IWM": ("^RUT", 1.0),
    "SOXX": ("^SOX", 2.0),
}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return None if math.isnan(out) or math.isinf(out) else out
    except Exception:
        return None


def _pct(new: float | None, old: float | None) -> float | None:
    return None if new is None or old in (None, 0) else (new - old) / old * 100.0


def _series(values: Any) -> list[float]:
    return [number for value in (values or []) if (number := _safe_float(value)) is not None]


def _volatility(closes: list[float]) -> float | None:
    returns = [(b - a) / a * 100.0 for a, b in zip(closes[:-1], closes[1:]) if a]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    return math.sqrt(sum((value - mean) ** 2 for value in returns) / (len(returns) - 1))


def _session_date(result: dict[str, Any], meta: dict[str, Any]) -> str | None:
    """Return the exchange-local date of the most recent daily bar."""
    timestamps = [int(value) for value in (result.get("timestamp") or []) if value]
    epoch = timestamps[-1] if timestamps else meta.get("regularMarketTime")
    if not epoch:
        return None
    try:
        timezone_name = str(meta.get("exchangeTimezoneName") or "America/New_York")
        return datetime.fromtimestamp(int(epoch), ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        return None


def fetch_asset_snapshot(ticker: str) -> dict[str, Any]:
    now = datetime.now(KST).isoformat(timespec="seconds")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d&events=div%2Csplits"
    try:
        response = requests.get(url, headers={"User-Agent": "telegram-news-aggregator/1.0"}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        result = (response.json().get("chart", {}).get("result") or [None])[0]
        if not isinstance(result, dict):
            raise RuntimeError("empty Yahoo chart result")
        meta = result.get("meta") or {}
        indicators = result.get("indicators") or {}
        quote = (indicators.get("quote") or [{}])[0]
        raw_closes = _series(quote.get("close") or [])
        adjusted = _series(((indicators.get("adjclose") or [{}])[0]).get("adjclose") or [])
        closes = adjusted or raw_closes
        price = _safe_float(meta.get("regularMarketPrice")) or (raw_closes[-1] if raw_closes else None)

        # chartPreviousClose describes the close before the requested chart range
        # on some Yahoo responses. Session returns must therefore use adjacent
        # daily bars rather than chartPreviousClose.
        daily_change = _pct(closes[-1], closes[-2]) if len(closes) >= 2 else None
        if daily_change is None:
            daily_change = _pct(price, _safe_float(meta.get("previousClose")))

        return {
            "ticker": ticker,
            "price": price,
            "change_pct": daily_change,
            "return_5d": _pct(closes[-1], closes[-6]) if len(closes) >= 6 else None,
            "return_20d": _pct(closes[-1], closes[-21]) if len(closes) >= 21 else None,
            "volatility_20d": _volatility(closes[-21:]),
            "session_date": _session_date(result, meta),
            "source": "Yahoo Finance daily bars",
            "timestamp": now,
            "error": None,
        }
    except Exception as exc:
        return {
            "ticker": ticker,
            "price": None,
            "change_pct": None,
            "return_5d": None,
            "return_20d": None,
            "volatility_20d": None,
            "session_date": None,
            "source": "Yahoo Finance daily bars",
            "timestamp": now,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _change(assets: dict[str, Any], ticker: str, *, validated: bool = False) -> float | None:
    item = assets.get(ticker) or {}
    if validated and "validated_change_pct" in item:
        return _safe_float(item.get("validated_change_pct"))
    return _safe_float(item.get("change_pct"))


def _validate_asset_changes(assets: dict[str, dict[str, Any]]) -> tuple[str | None, list[str]]:
    """Reject stale-session or implausibly divergent ETF returns before regime/flow use."""
    anchor = str(
        (assets.get("^GSPC") or {}).get("session_date")
        or (assets.get("SPY") or {}).get("session_date")
        or ""
    ).strip() or None
    warnings: list[str] = []

    for ticker, item in assets.items():
        raw = _safe_float(item.get("change_pct"))
        item["validated_change_pct"] = raw
        item["validation_status"] = "ok" if raw is not None else "missing"
        item["validation_reason"] = ""

        if ticker not in US_VALIDATION_TICKERS or raw is None:
            continue

        session = str(item.get("session_date") or "").strip() or None
        if anchor and session and session != anchor:
            item["validated_change_pct"] = None
            item["validation_status"] = "invalid"
            item["validation_reason"] = f"session_mismatch:{session}!={anchor}"
            warnings.append(f"{ticker} 세션 불일치({session} vs {anchor})")
            continue

        benchmark = BENCHMARK_PAIRS.get(ticker)
        if not benchmark:
            continue
        benchmark_ticker, max_gap = benchmark
        peer = assets.get(benchmark_ticker) or {}
        peer_change = _safe_float(peer.get("change_pct"))
        peer_session = str(peer.get("session_date") or "").strip() or None
        if peer_change is None or (session and peer_session and session != peer_session):
            continue

        gap = abs(raw - peer_change)
        if gap > max_gap:
            item["validated_change_pct"] = None
            item["validation_status"] = "invalid"
            item["validation_reason"] = f"benchmark_divergence:{benchmark_ticker}:{gap:.2f}pp"
            warnings.append(
                f"{ticker} {raw:+.2f}% vs {benchmark_ticker} {peer_change:+.2f}% 괴리 {gap:.2f}%p"
            )

    return anchor, warnings


def collect_global_snapshot() -> dict[str, Any]:
    tickers = list(TRADE_ASSETS) + list(GLOBAL_PROXIES)
    assets: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="global-market") as pool:
        futures = {pool.submit(fetch_asset_snapshot, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            item = future.result()
            assets[item["ticker"]] = item

    validated_session_date, validation_warnings = _validate_asset_changes(assets)

    signals: list[float] = []
    for ticker in RISK_TICKERS:
        item = assets.get(ticker) or {}
        if item.get("validation_status") == "invalid":
            continue
        daily = _change(assets, ticker, validated=True)
        if daily is not None:
            signals.append(max(-2.0, min(2.0, daily)))
        value_5d = _safe_float(item.get("return_5d"))
        if value_5d is not None:
            signals.append(max(-2.0, min(2.0, value_5d * 0.35)))
    vix = _change(assets, "^VIX", validated=True)
    tnx = _change(assets, "^TNX", validated=True)
    if vix is not None:
        signals.append(max(-2.0, min(2.0, -vix * 0.25)))
    if tnx is not None:
        signals.append(max(-1.5, min(1.5, -tnx * 0.15)))
    score = sum(signals) / len(signals) if signals else 0.0
    regime, label = ("risk_on", "위험선호") if score >= 0.65 else ("risk_off", "위험회피") if score <= -0.65 else ("mixed", "혼조")

    regions = []
    for ticker, label_name in GLOBAL_PROXIES.items():
        if ticker in {"HYG", "^VIX", "^TNX", "DX-Y.NYB", "^GSPC", "^IXIC", "^RUT", "^SOX"}:
            continue
        value = _change(assets, ticker)
        if value is not None:
            regions.append(f"{label_name} {value:+.2f}%")
    flows = []
    for ticker, label_name in (("QQQ", "성장주"), ("IWM", "중소형"), ("EEM", "신흥국"), ("HYG", "하이일드"), ("GLD", "금"), ("TLT", "장기채")):
        value = _change(assets, ticker, validated=True)
        if value is not None:
            flows.append(f"{label_name} {value:+.2f}%")

    return {
        "timestamp": datetime.now(KST).isoformat(timespec="seconds"),
        "assets": assets,
        "regime": regime,
        "regime_label": label,
        "regime_score": round(score, 3),
        "regions": " / ".join(regions) if regions else "지역별 지수 확인불가",
        "flow_proxy": " / ".join(flows) if flows else "ETF·가격 흐름 확인불가",
        "data_quality": sum(1 for item in assets.values() if item.get("price") is not None),
        "requested_assets": len(tickers),
        "validated_session_date": validated_session_date,
        "validation_warnings": validation_warnings,
    }
