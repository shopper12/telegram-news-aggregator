from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from functools import lru_cache

import requests


BAD_TICKERS = {
    "AI", "SK", "KV", "ETF", "CEO", "SEC", "FED", "FOMC", "GDP", "CPI",
    "KOSPI", "KOSDAQ", "KRX", "NYSE", "NASDAQ", "IPO", "MOU", "IR", "PR",
    "USA", "US", "EU", "UK", "CN", "JP", "KR", "USD", "KRW", "CEO", "CFO",
    "ADR", "ADS", "THE", "AND", "FOR", "INC", "LTD", "LLC", "PLC", "NEW", "OLD",
}

UPPER_TICKER_RE = re.compile(r"\b[A-Z]{1,12}\b")
KR_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# 전 종목 카탈로그가 실패하거나 한국어 별칭이 다른 경우 보완하는 핵심 별칭.
COMMON_ALIASES = {
    "엔비디아": ("엔비디아", "NVDA", "stock_us"),
    "NVIDIA": ("엔비디아", "NVDA", "stock_us"),
    "테슬라": ("테슬라", "TSLA", "stock_us"),
    "TESLA": ("테슬라", "TSLA", "stock_us"),
    "애플": ("애플", "AAPL", "stock_us"),
    "마이크로소프트": ("마이크로소프트", "MSFT", "stock_us"),
    "MS": ("마이크로소프트", "MSFT", "stock_us"),
    "구글": ("알파벳", "GOOGL", "stock_us"),
    "알파벳": ("알파벳", "GOOGL", "stock_us"),
    "아마존": ("아마존", "AMZN", "stock_us"),
    "메타": ("메타", "META", "stock_us"),
    "페이스북": ("메타", "META", "stock_us"),
    "브로드컴": ("브로드컴", "AVGO", "stock_us"),
    "TSMC": ("TSMC", "TSM", "stock_us"),
    "팔란티어": ("팔란티어", "PLTR", "stock_us"),
    "AMD": ("AMD", "AMD", "stock_us"),
    "인텔": ("인텔", "INTC", "stock_us"),
    "오라클": ("오라클", "ORCL", "stock_us"),
    "넷플릭스": ("넷플릭스", "NFLX", "stock_us"),
    "월마트": ("월마트", "WMT", "stock_us"),
    "코스트코": ("코스트코", "COST", "stock_us"),
    "마이크론": ("마이크론", "MU", "stock_us"),
    "슈퍼마이크로": ("슈퍼마이크로", "SMCI", "stock_us"),
    "SMCI": ("슈퍼마이크로", "SMCI", "stock_us"),
    "일라이릴리": ("일라이릴리", "LLY", "stock_us"),
    "노보노디스크": ("노보노디스크", "NVO", "stock_us"),
    "비트코인": ("비트코인", "BTC", "crypto"),
    "bitcoin": ("Bitcoin", "BTC", "crypto"),
    "btc": ("Bitcoin", "BTC", "crypto"),
    "이더리움": ("이더리움", "ETH", "crypto"),
    "ethereum": ("Ethereum", "ETH", "crypto"),
    "eth": ("Ethereum", "ETH", "crypto"),
    "솔라나": ("솔라나", "SOL", "crypto"),
    "solana": ("Solana", "SOL", "crypto"),
    "리플": ("리플", "XRP", "crypto"),
    "ripple": ("Ripple", "XRP", "crypto"),
    "수이": ("SUI", "SUI", "crypto"),
    "온도": ("ONDO", "ONDO", "crypto"),
    "렌더": ("RENDER", "RENDER", "crypto"),
}


@dataclass(frozen=True)
class ResolvedSymbol:
    name: str
    ticker: str
    asset_type: str  # stock_kr, stock_us, crypto, stock_or_us


@dataclass(frozen=True)
class SymbolEntry:
    name: str
    ticker: str
    asset_type: str
    aliases: tuple[str, ...]


def _safe_get(url: str, timeout: int = 10) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": "telegram-news-aggregator/0.1"}, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def _normalize_company_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\b(Common Stock|Ordinary Shares|American Depositary Shares|American Depositary Receipt|ADS|ADR)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(Class [A-Z]|Class A Common Stock|Class B Common Stock)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|Limited|PLC|LLC|N\.V\.|S\.A\.)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip(" -,")
    return name or name


@lru_cache(maxsize=1)
def _load_krx_catalog() -> list[SymbolEntry]:
    try:
        from pykrx import stock  # type: ignore
    except Exception:
        return []

    entries: list[SymbolEntry] = []
    for market, suffix in [("KOSPI", ".KS"), ("KOSDAQ", ".KQ"), ("KONEX", ".KQ")]:
        try:
            tickers = stock.get_market_ticker_list(market=market)
        except Exception:
            continue
        for code in tickers:
            try:
                name = stock.get_market_ticker_name(code)
            except Exception:
                continue
            if not name:
                continue
            compact = name.replace(" ", "")
            aliases = tuple(dict.fromkeys((name, compact, code, f"{code}{suffix}")))
            entries.append(SymbolEntry(name=name, ticker=f"{code}{suffix}", asset_type="stock_kr", aliases=aliases))
    return entries


@lru_cache(maxsize=1)
def _load_us_catalog() -> list[SymbolEntry]:
    urls = [
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    ]
    entries: list[SymbolEntry] = []
    seen: set[str] = set()

    for url in urls:
        text = _safe_get(url)
        if not text:
            continue
        rows = list(csv.DictReader(io.StringIO(text), delimiter="|"))
        for row in rows:
            symbol = (row.get("Symbol") or row.get("ACT Symbol") or "").strip()
            name = (row.get("Security Name") or "").strip()
            if not symbol or not name or symbol == "File Creation Time":
                continue
            symbol = symbol.replace("$", "").replace(".", "-").upper()
            if not symbol or symbol in BAD_TICKERS or symbol in seen:
                continue
            seen.add(symbol)
            base_name = _normalize_company_name(name.split(" - ")[0].strip())
            aliases = tuple(dict.fromkeys((symbol, base_name, name.split(" - ")[0].strip())))
            entries.append(SymbolEntry(name=base_name, ticker=symbol, asset_type="stock_us", aliases=aliases))
    return entries


@lru_cache(maxsize=1)
def _load_crypto_catalog() -> list[SymbolEntry]:
    entries: list[SymbolEntry] = []
    seen: set[str] = set()

    text = _safe_get("https://api.upbit.com/v1/market/all?isDetails=false")
    if text:
        try:
            data = requests.models.complexjson.loads(text)
            for item in data:
                market = item.get("market", "")
                parts = market.split("-")
                if len(parts) != 2:
                    continue
                base = parts[1].upper()
                korean_name = item.get("korean_name") or base
                english_name = item.get("english_name") or base
                if base in seen:
                    continue
                seen.add(base)
                entries.append(SymbolEntry(name=korean_name, ticker=base, asset_type="crypto", aliases=(base, korean_name, english_name)))
        except Exception:
            pass

    text = _safe_get("https://api.binance.com/api/v3/exchangeInfo")
    if text:
        try:
            data = requests.models.complexjson.loads(text)
            for item in data.get("symbols", []):
                base = str(item.get("baseAsset") or "").upper()
                quote = str(item.get("quoteAsset") or "").upper()
                if quote not in {"USDT", "FDUSD", "USDC", "BTC", "ETH"}:
                    continue
                if not base or base in seen or base in BAD_TICKERS:
                    continue
                seen.add(base)
                entries.append(SymbolEntry(name=base, ticker=base, asset_type="crypto", aliases=(base,)))
        except Exception:
            pass

    for alias, (name, ticker, asset_type) in COMMON_ALIASES.items():
        if asset_type == "crypto" and ticker not in seen:
            seen.add(ticker)
            entries.append(SymbolEntry(name=name, ticker=ticker, asset_type=asset_type, aliases=(alias, name, ticker)))

    return entries


@lru_cache(maxsize=1)
def _catalogs() -> tuple[list[SymbolEntry], list[SymbolEntry], list[SymbolEntry]]:
    return _load_krx_catalog(), _load_us_catalog(), _load_crypto_catalog()


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    if alias.isascii() and alias.replace("-", "").isalnum():
        return re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE) is not None
    return alias.lower() in text.lower()


def _append(result: list[ResolvedSymbol], seen: set[str], entry: SymbolEntry) -> None:
    if entry.ticker in seen:
        return
    result.append(ResolvedSymbol(name=entry.name, ticker=entry.ticker, asset_type=entry.asset_type))
    seen.add(entry.ticker)


def _find_entry_by_kr_code(code: str, krx_catalog: list[SymbolEntry]) -> SymbolEntry | None:
    for entry in krx_catalog:
        if entry.ticker.startswith(code):
            return entry
    return None


def resolve_symbols(text: str, categories: list[str] | None = None, raw_tickers: list[str] | None = None) -> list[ResolvedSymbol]:
    categories = categories or []
    raw_tickers = raw_tickers or []
    result: list[ResolvedSymbol] = []
    seen: set[str] = set()

    is_crypto_context = "crypto" in categories or any(
        word in text.lower() for word in ["btc", "비트코인", "코인", "온체인", "업비트", "바이낸스", "usdt"]
    )

    krx_catalog, us_catalog, crypto_catalog = _catalogs()

    for alias, (name, ticker, asset_type) in COMMON_ALIASES.items():
        if _contains_alias(text, alias):
            _append(result, seen, SymbolEntry(name=name, ticker=ticker, asset_type=asset_type, aliases=(alias,)))

    for code in KR_CODE_RE.findall(text):
        entry = _find_entry_by_kr_code(code, krx_catalog)
        if entry:
            _append(result, seen, entry)

    for entry in krx_catalog:
        if any(_contains_alias(text, alias) for alias in entry.aliases):
            _append(result, seen, entry)

    for entry in crypto_catalog:
        if any(_contains_alias(text, alias) for alias in entry.aliases):
            _append(result, seen, entry)

    # 미국 종목: 티커 직접 언급은 전 종목 대응. 회사명 매칭은 짧은 일반명 오탐을 줄이기 위해 5자 이상만 허용.
    for entry in us_catalog:
        name_aliases = [a for a in entry.aliases if a != entry.ticker]
        if len(entry.name) >= 5 and any(_contains_alias(text, alias) for alias in name_aliases):
            _append(result, seen, entry)

    crypto_tickers = {entry.ticker for entry in crypto_catalog}
    us_by_ticker = {entry.ticker: entry for entry in us_catalog}
    for ticker in sorted(set(raw_tickers + UPPER_TICKER_RE.findall(text))):
        ticker = ticker.upper().strip()
        if ticker in BAD_TICKERS or ticker in seen:
            continue
        if ticker in crypto_tickers or is_crypto_context:
            asset_type = "crypto"
            result.append(ResolvedSymbol(name=ticker, ticker=ticker, asset_type=asset_type))
            seen.add(ticker)
        elif ticker in us_by_ticker:
            _append(result, seen, us_by_ticker[ticker])
        elif len(ticker) >= 2:
            result.append(ResolvedSymbol(name=ticker, ticker=ticker, asset_type="stock_or_us"))
            seen.add(ticker)

    return result
