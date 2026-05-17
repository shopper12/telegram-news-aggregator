from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import unquote

import requests


# 대문자 약어 중 종목/코인 티커로 오인하기 쉬운 단어
BAD_TICKERS = {
    "AI", "SK", "KV", "ETF", "CEO", "SEC", "FED", "FOMC", "GDP", "CPI",
    "KOSPI", "KOSDAQ", "KRX", "NYSE", "NASDAQ", "IPO", "MOU", "IR", "PR",
    "USA", "US", "EU", "UK", "CN", "JP", "KR", "USD", "KRW", "CEO", "CFO",
}

UPPER_TICKER_RE = re.compile(r"\b[A-Z]{2,12}\b")

# 한국어 뉴스에서 자주 쓰는 해외주식/크립토 별칭. 전 종목 DB를 보완하는 용도.
COMMON_ALIASES = {
    "엔비디아": ("NVIDIA", "NVDA", "stock_us"),
    "NVIDIA": ("NVIDIA", "NVDA", "stock_us"),
    "테슬라": ("Tesla", "TSLA", "stock_us"),
    "TESLA": ("Tesla", "TSLA", "stock_us"),
    "애플": ("Apple", "AAPL", "stock_us"),
    "마이크로소프트": ("Microsoft", "MSFT", "stock_us"),
    "구글": ("Alphabet", "GOOGL", "stock_us"),
    "알파벳": ("Alphabet", "GOOGL", "stock_us"),
    "아마존": ("Amazon", "AMZN", "stock_us"),
    "메타": ("Meta", "META", "stock_us"),
    "브로드컴": ("Broadcom", "AVGO", "stock_us"),
    "TSMC": ("TSMC", "TSM", "stock_us"),
    "팔란티어": ("Palantir", "PLTR", "stock_us"),
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


@lru_cache(maxsize=1)
def _load_krx_catalog() -> list[SymbolEntry]:
    """KRX 전체 종목명 ↔ 티커 자동 매핑.

    pykrx가 실패하면 빈 리스트를 반환한다. GitHub Actions에서는 requirements의 pykrx를 사용한다.
    """
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
            entries.append(
                SymbolEntry(
                    name=name,
                    ticker=f"{code}{suffix}",
                    asset_type="stock_kr",
                    aliases=(name, name.replace(" ", "")),
                )
            )
    return entries


@lru_cache(maxsize=1)
def _load_us_catalog() -> list[SymbolEntry]:
    """NASDAQ/NYSE/AMEX 등 미국 상장 티커 catalog.

    회사명 전체 매칭과 대문자 티커 매칭을 보조한다. 한국어 별칭은 COMMON_ALIASES가 보완한다.
    """
    urls = [
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
    ]
    entries: list[SymbolEntry] = []

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
            symbol = symbol.replace("$", "").replace(".", "-")
            if not symbol or symbol in BAD_TICKERS:
                continue
            base_name = name.split(" - ")[0].strip()
            entries.append(
                SymbolEntry(
                    name=base_name,
                    ticker=symbol,
                    asset_type="stock_us",
                    aliases=(symbol, base_name),
                )
            )
    return entries


@lru_cache(maxsize=1)
def _load_crypto_catalog() -> list[SymbolEntry]:
    entries: list[SymbolEntry] = []
    seen: set[str] = set()

    # Upbit: KRW/BTC/USDT 마켓 전체와 한글명/영문명 제공
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
                entries.append(
                    SymbolEntry(
                        name=korean_name,
                        ticker=base,
                        asset_type="crypto",
                        aliases=(base, korean_name, english_name),
                    )
                )
        except Exception:
            pass

    # Binance: baseAsset 기준으로 글로벌 코인 티커 보강
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
                entries.append(
                    SymbolEntry(
                        name=base,
                        ticker=base,
                        asset_type="crypto",
                        aliases=(base,),
                    )
                )
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


def resolve_symbols(text: str, categories: list[str] | None = None, raw_tickers: list[str] | None = None) -> list[ResolvedSymbol]:
    categories = categories or []
    raw_tickers = raw_tickers or []
    result: list[ResolvedSymbol] = []
    seen: set[str] = set()

    is_crypto_context = "crypto" in categories or any(
        word in text.lower() for word in ["btc", "비트코인", "코인", "온체인", "업비트", "바이낸스", "usdt"]
    )

    krx_catalog, us_catalog, crypto_catalog = _catalogs()

    # 1) 별칭 우선 매칭
    for alias, (name, ticker, asset_type) in COMMON_ALIASES.items():
        if _contains_alias(text, alias):
            _append(result, seen, SymbolEntry(name=name, ticker=ticker, asset_type=asset_type, aliases=(alias,)))

    # 2) KRX 전체 종목명 매칭
    for entry in krx_catalog:
        if any(_contains_alias(text, alias) for alias in entry.aliases):
            _append(result, seen, entry)

    # 3) Crypto 전체 마켓명/티커 매칭
    for entry in crypto_catalog:
        if any(_contains_alias(text, alias) for alias in entry.aliases):
            _append(result, seen, entry)

    # 4) US 회사명은 너무 느슨하면 오탐이 커서, 이름 길이가 충분한 경우만 매칭
    for entry in us_catalog:
        if len(entry.name) >= 5 and any(_contains_alias(text, alias) for alias in entry.aliases):
            _append(result, seen, entry)

    # 5) 원문 대문자 티커 보강
    crypto_tickers = {entry.ticker for entry in crypto_catalog}
    us_tickers = {entry.ticker for entry in us_catalog}
    for ticker in sorted(set(raw_tickers + UPPER_TICKER_RE.findall(text))):
        ticker = ticker.upper().strip()
        if ticker in BAD_TICKERS or ticker in seen:
            continue
        if ticker in crypto_tickers or is_crypto_context:
            asset_type = "crypto"
        elif ticker in us_tickers:
            asset_type = "stock_us"
        else:
            asset_type = "stock_or_us"
        result.append(ResolvedSymbol(name=ticker, ticker=ticker, asset_type=asset_type))
        seen.add(ticker)

    return result
