from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo
import math

import requests


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    source: str
    timestamp: str
    error: str | None = None


@dataclass(frozen=True)
class Strategy:
    quote: Quote | None
    view: str
    entry: str
    stop: str
    target: str
    risk: str


def _now_kst() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
        if value == "":
            return None
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except Exception:
        return None


def _fmt_price(price: float | None) -> str:
    if price is None:
        return "가격확인불가"
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    return f"{price:,.6f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return ""
    return f" {value:+.2f}%"


def _json_get(url: str, timeout: int = 8) -> object | None:
    try:
        resp = requests.get(url, headers={"User-Agent": "telegram-news-aggregator/0.1"}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


@lru_cache(maxsize=256)
def fetch_quote(ticker: str, asset_type: str) -> Quote:
    ticker = ticker.strip().upper()
    if asset_type == "crypto":
        return _fetch_crypto_quote(ticker)
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return _fetch_kr_stock_quote(ticker)
    return _fetch_us_quote(ticker)


def _fetch_kr_stock_quote(ticker: str) -> Quote:
    code = ticker.split(".")[0]
    url = f"https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{code}"
    data = _json_get(url)
    if not isinstance(data, dict):
        return Quote(ticker, None, None, None, "Naver Finance", _now_kst(), "Naver quote fetch failed")

    try:
        areas = data.get("result", {}).get("areas", [])
        datas = areas[0].get("datas", []) if areas else []
        item = datas[0] if datas else {}
        price = _safe_float(item.get("nv"))
        change_pct = _safe_float(item.get("cr"))
        turnover = _safe_float(item.get("aa") or item.get("aqnt"))
        return Quote(ticker, price, change_pct, turnover, "Naver Finance", _now_kst())
    except Exception as exc:
        return Quote(ticker, None, None, None, "Naver Finance", _now_kst(), str(exc))


def _fetch_pykrx_index(label: str, index_code: str, sanity_low: float, sanity_high: float) -> Quote:
    try:
        from pykrx import stock  # type: ignore

        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        start = (today - timedelta(days=14)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        df = stock.get_index_ohlcv_by_date(start, end, index_code)
        if df is None or df.empty:
            return Quote(label, None, None, None, "pykrx", _now_kst(), "empty index frame")
        closes = df["종가"].dropna()
        if len(closes) < 2:
            return Quote(label, None, None, None, "pykrx", _now_kst(), "not enough index closes")
        price = float(closes.iloc[-1])
        prev = float(closes.iloc[-2])
        if not sanity_low <= price <= sanity_high:
            return Quote(label, None, None, None, "pykrx", _now_kst(), "index sanity check failed")
        change_pct = ((price - prev) / prev * 100) if prev else None
        return Quote(label, price, change_pct, None, "pykrx", _now_kst())
    except Exception as exc:
        return Quote(label, None, None, None, "pykrx", _now_kst(), str(exc))


def _fetch_us_quote(ticker: str) -> Quote:
    yahoo_ticker = ticker.replace(".", "-")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}?range=5d&interval=5m"
    data = _json_get(url)
    try:
        result = data.get("chart", {}).get("result", [])[0] if isinstance(data, dict) else None
        meta = result.get("meta", {}) if result else {}
        price = _safe_float(meta.get("regularMarketPrice"))
        prev = _safe_float(meta.get("chartPreviousClose") or meta.get("previousClose"))
        volume = _safe_float(meta.get("regularMarketVolume"))
        change_pct = ((price - prev) / prev * 100) if price is not None and prev else None
        return Quote(ticker, price, change_pct, volume, "Yahoo Finance", _now_kst())
    except Exception as exc:
        return Quote(ticker, None, None, None, "Yahoo Finance", _now_kst(), str(exc))


def _fetch_crypto_quote(ticker: str) -> Quote:
    ticker = ticker.upper()
    upbit_market = f"KRW-{ticker}"
    upbit = _json_get(f"https://api.upbit.com/v1/ticker?markets={upbit_market}")
    if isinstance(upbit, list) and upbit:
        item = upbit[0]
        price = _safe_float(item.get("trade_price"))
        change_pct_raw = _safe_float(item.get("signed_change_rate"))
        change_pct = change_pct_raw * 100 if change_pct_raw is not None else None
        turnover = _safe_float(item.get("acc_trade_price_24h"))
        return Quote(ticker, price, change_pct, turnover, f"Upbit {upbit_market}", _now_kst())

    binance_symbol = f"{ticker}USDT"
    data = _json_get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={binance_symbol}")
    if isinstance(data, dict) and data.get("lastPrice"):
        price = _safe_float(data.get("lastPrice"))
        change_pct = _safe_float(data.get("priceChangePercent"))
        turnover = _safe_float(data.get("quoteVolume"))
        return Quote(ticker, price, change_pct, turnover, f"Binance {binance_symbol}", _now_kst())

    return Quote(ticker, None, None, None, "Upbit/Binance", _now_kst(), "crypto quote fetch failed")


INDEX_MAP = {
    "KOSPI": "KRX:KOSPI",
    "KOSDAQ": "KRX:KOSDAQ",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
    "USD/KRW": "KRW=X",
}


def _valid_quote(label: str, quote: Quote) -> Quote:
    if quote.price is None:
        return quote
    ranges = {
        "KOSPI": (1800, 5000),
        "KOSDAQ": (400, 1500),
        "S&P500": (3000, 9000),
        "NASDAQ": (8000, 40000),
        "USD/KRW": (900, 1800),
    }
    low, high = ranges.get(label, (float("-inf"), float("inf")))
    if not low <= quote.price <= high:
        return Quote(quote.ticker, None, None, None, quote.source, quote.timestamp, f"{label} sanity check failed")
    if quote.change_pct is not None and abs(quote.change_pct) > 15:
        return Quote(quote.ticker, None, None, None, quote.source, quote.timestamp, f"{label} change sanity check failed")
    return quote


def _fetch_index(label: str, ticker: str) -> Quote:
    if ticker == "KRX:KOSPI":
        return _fetch_pykrx_index("KOSPI", "1001", 1800, 5000)
    if ticker == "KRX:KOSDAQ":
        return _fetch_pykrx_index("KOSDAQ", "2001", 400, 1500)
    return _valid_quote(label, _fetch_us_quote(ticker))


@lru_cache(maxsize=1)
def fetch_market_overview() -> list[str]:
    lines: list[str] = []
    for label, ticker in INDEX_MAP.items():
        quote = _fetch_index(label, ticker)
        if quote.price is None:
            continue
        lines.append(f"{label} {_fmt_price(quote.price)}{_fmt_pct(quote.change_pct)}")
    if not lines:
        return ["시장지표 확인불가"]
    return lines[:5]


def get_market_context() -> dict | None:
    try:
        kospi = _fetch_index("KOSPI", "KRX:KOSPI")
        kosdaq = _fetch_index("KOSDAQ", "KRX:KOSDAQ")
        sp500 = _fetch_index("S&P500", "^GSPC")
        nasdaq = _fetch_index("NASDAQ", "^IXIC")
        usdkrw = _fetch_index("USD/KRW", "KRW=X")
        result = {
            "kospi_price": kospi.price,
            "kospi_change_pct": kospi.change_pct,
            "kosdaq_price": kosdaq.price,
            "kosdaq_change_pct": kosdaq.change_pct,
            "sp500_price": sp500.price,
            "sp500_change_pct": sp500.change_pct,
            "nasdaq_price": nasdaq.price,
            "nasdaq_change_pct": nasdaq.change_pct,
            "usd_krw": usdkrw.price,
        }
        valid_count = sum(1 for value in result.values() if value is not None)
        return result if valid_count >= 2 else None
    except Exception:
        return None


def build_strategy(ticker: str, asset_type: str, news_score: int, risk_text: str) -> Strategy:
    quote = fetch_quote(ticker, asset_type)
    if quote.price is None:
        return Strategy(
            quote=quote,
            view="가격 데이터 확인 실패",
            entry="진입 보류",
            stop="손절가 산출 불가",
            target="목표가 산출 불가",
            risk=f"{risk_text} / 가격 소스 오류: {quote.error or 'unknown'}",
        )

    price = quote.price
    change = quote.change_pct or 0.0
    is_crypto = asset_type == "crypto"

    if is_crypto:
        entry_price = price * 1.006
        stop_price = price * 0.982
        target_price = price * 1.035
    else:
        entry_price = price * 1.01
        stop_price = price * 0.97
        target_price = price * 1.05

    if news_score >= 9 and change >= 0:
        view = "강한 뉴스 + 가격 양호. 돌파 확인형 대응"
    elif news_score >= 7 and change < 0:
        view = "뉴스는 강하지만 가격 약세. 반등 확인 전 추격 금지"
    else:
        view = "관심 후보. 가격·거래대금 확인 우선"

    return Strategy(
        quote=quote,
        view=view,
        entry=f"현재가 {_fmt_price(price)} 기준, {_fmt_price(entry_price)} 돌파 + 거래대금 증가 확인",
        stop=f"{_fmt_price(stop_price)} 이탈 시 무효",
        target=f"1차 {_fmt_price(target_price)} / 급등 시 분할매도",
        risk=f"{risk_text} / 현재 등락률 {_fmt_pct(change)} / 소스 {quote.source} {quote.timestamp}",
    )
