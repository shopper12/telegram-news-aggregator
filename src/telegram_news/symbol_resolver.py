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
    "USA", "US", "EU", "UK", "CN", "JP", "KR", "USD", "KRW", "CFO", "ADR", "ADS",
    "THE", "AND", "FOR", "INC", "LTD", "LLC", "PLC", "NEW", "OLD", "EV", "DD", "DB",
    "ON", "IT", "BE", "OR", "TO", "IN", "AS", "AT", "BY", "IS", "ESS", "NIM", "GLP",
}

UPPER_TICKER_RE = re.compile(r"\b[A-Z]{1,12}\b")
STRICT_TICKER_RE = re.compile(r"(?:(?<=\$)|(?<=NASDAQ:)|(?<=NYSE:)|(?<=AMEX:)|(?<=Nasdaq:)|(?<=NYSE American:))([A-Z]{1,8})\b|\(([A-Z]{1,8})\)")
KR_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
US_TICKER_CONTEXT_WORDS = {
    "nyse", "nasdaq", "amex", "ticker", "티커", "미국주식", "미장", "reddit", "레딧",
    "wallstreetbets", "wsb", "stock", "stocks", "shares", "equity",
}

COMMON_ALIASES = {
    # US mega/semis
    "엔비디아": ("엔비디아", "NVDA", "stock_us"),
    "nvidia": ("엔비디아", "NVDA", "stock_us"),
    "테슬라": ("테슬라", "TSLA", "stock_us"),
    "tesla": ("테슬라", "TSLA", "stock_us"),
    "애플": ("애플", "AAPL", "stock_us"),
    "apple": ("애플", "AAPL", "stock_us"),
    "마이크로소프트": ("마이크로소프트", "MSFT", "stock_us"),
    "microsoft": ("마이크로소프트", "MSFT", "stock_us"),
    "구글": ("알파벳", "GOOGL", "stock_us"),
    "알파벳": ("알파벳", "GOOGL", "stock_us"),
    "아마존": ("아마존", "AMZN", "stock_us"),
    "메타": ("메타", "META", "stock_us"),
    "브로드컴": ("브로드컴", "AVGO", "stock_us"),
    "tsmc": ("TSMC", "TSM", "stock_us"),
    "팔란티어": ("팔란티어", "PLTR", "stock_us"),
    "amd": ("AMD", "AMD", "stock_us"),
    "인텔": ("인텔", "INTC", "stock_us"),
    "오라클": ("오라클", "ORCL", "stock_us"),
    "넷플릭스": ("넷플릭스", "NFLX", "stock_us"),
    "마이크론": ("마이크론", "MU", "stock_us"),
    "슈퍼마이크로": ("슈퍼마이크로", "SMCI", "stock_us"),
    "일라이릴리": ("일라이릴리", "LLY", "stock_us"),
    "노보노디스크": ("노보노디스크", "NVO", "stock_us"),

    # KR frequently traded names and aliases. Used even when pykrx is unavailable or slow.
    "삼성전자": ("삼성전자", "005930.KS", "stock_kr"),
    "삼전": ("삼성전자", "005930.KS", "stock_kr"),
    "sk하이닉스": ("SK하이닉스", "000660.KS", "stock_kr"),
    "하이닉스": ("SK하이닉스", "000660.KS", "stock_kr"),
    "현대차": ("현대차", "005380.KS", "stock_kr"),
    "현대자동차": ("현대차", "005380.KS", "stock_kr"),
    "기아": ("기아", "000270.KS", "stock_kr"),
    "naver": ("NAVER", "035420.KS", "stock_kr"),
    "네이버": ("NAVER", "035420.KS", "stock_kr"),
    "카카오": ("카카오", "035720.KS", "stock_kr"),
    "lg전자": ("LG전자", "066570.KS", "stock_kr"),
    "lg에너지솔루션": ("LG에너지솔루션", "373220.KS", "stock_kr"),
    "셀트리온": ("셀트리온", "068270.KS", "stock_kr"),
    "삼성바이오로직스": ("삼성바이오로직스", "207940.KS", "stock_kr"),
    "두산에너빌리티": ("두산에너빌리티", "034020.KS", "stock_kr"),
    "한미반도체": ("한미반도체", "042700.KS", "stock_kr"),
    "에코프로": ("에코프로", "086520.KQ", "stock_kr"),
    "에코프로비엠": ("에코프로비엠", "247540.KQ", "stock_kr"),
    "파워넷": ("파워넷", "037030.KQ", "stock_kr"),
    "성호전자": ("성호전자", "043260.KQ", "stock_kr"),
    "비나텍": ("비나텍", "126340.KQ", "stock_kr"),
    "삼지전자": ("삼지전자", "037460.KQ", "stock_kr"),
    "아모텍": ("아모텍", "052710.KQ", "stock_kr"),
    "대한전선": ("대한전선", "001440.KS", "stock_kr"),
    "sk오션플랜트": ("SK오션플랜트", "100090.KS", "stock_kr"),
    "한온시스템": ("한온시스템", "018880.KS", "stock_kr"),
    "하림지주": ("하림지주", "003380.KQ", "stock_kr"),
    "두산로보틱스": ("두산로보틱스", "454910.KS", "stock_kr"),
    "한화시스템": ("한화시스템", "272210.KS", "stock_kr"),
    "효성중공업": ("효성중공업", "298040.KS", "stock_kr"),
    "한국항공우주": ("한국항공우주", "047810.KS", "stock_kr"),
    "kai": ("한국항공우주", "047810.KS", "stock_kr"),
    "미래에셋생명": ("미래에셋생명", "085620.KS", "stock_kr"),
    "제주반도체": ("제주반도체", "080220.KQ", "stock_kr"),
    "hpsp": ("HPSP", "403870.KQ", "stock_kr"),
    "씨에스윈드": ("씨에스윈드", "112610.KS", "stock_kr"),
    "현대모비스": ("현대모비스", "012330.KS", "stock_kr"),
    "현대로템": ("현대로템", "064350.KS", "stock_kr"),
    "hd한국조선해양": ("HD한국조선해양", "009540.KS", "stock_kr"),
    "한국조선해양": ("HD한국조선해양", "009540.KS", "stock_kr"),
    "hd현대일렉트릭": ("HD현대일렉트릭", "267260.KS", "stock_kr"),
    "ls": ("LS", "006260.KS", "stock_kr"),
    "ls electric": ("LS ELECTRIC", "010120.KS", "stock_kr"),
    "ls일렉트릭": ("LS ELECTRIC", "010120.KS", "stock_kr"),
    "일진전기": ("일진전기", "103590.KS", "stock_kr"),
    "씨에스베어링": ("씨에스베어링", "297090.KQ", "stock_kr"),
    "디에스케이": ("디에스케이", "109740.KQ", "stock_kr"),

    # Crypto aliases are retained for blocking/filtering, but stock reports drop crypto symbols.
    "비트코인": ("비트코인", "BTC", "crypto"),
    "bitcoin": ("Bitcoin", "BTC", "crypto"),
    "btc": ("Bitcoin", "BTC", "crypto"),
    "이더리움": ("이더리움", "ETH", "crypto"),
    "ethereum": ("Ethereum", "ETH", "crypto"),
    "eth": ("Ethereum", "ETH", "crypto"),
    "솔라나": ("솔라나", "SOL", "crypto"),
    "리플": ("리플", "XRP", "crypto"),
    "수이": ("SUI", "SUI", "crypto"),
}


@dataclass(frozen=True)
class ResolvedSymbol:
    name: str
    ticker: str
    asset_type: str


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
    original = re.sub(r"\s+", " ", name).strip()
    cleaned = re.sub(r"\b(Common Stock|Ordinary Shares|American Depositary Shares|American Depositary Receipt|ADS|ADR)\b", "", original, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(Class [A-Z]|Class A Common Stock|Class B Common Stock)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|Limited|PLC|LLC|N\.V\.|S\.A\.)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,")
    return cleaned or original


def _normal_alias(value: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", str(value or "").lower())


@lru_cache(maxsize=1)
def _manual_stock_entries() -> list[SymbolEntry]:
    out: list[SymbolEntry] = []
    seen: set[str] = set()
    for alias, (name, ticker, asset_type) in COMMON_ALIASES.items():
        if asset_type not in {"stock_kr", "stock_us"}:
            continue
        if ticker in seen:
            for idx, entry in enumerate(out):
                if entry.ticker == ticker:
                    aliases = tuple(dict.fromkeys((*entry.aliases, alias, name, ticker, _normal_alias(alias), _normal_alias(name))))
                    out[idx] = SymbolEntry(entry.name, entry.ticker, entry.asset_type, aliases)
                    break
            continue
        seen.add(ticker)
        out.append(SymbolEntry(name=name, ticker=ticker, asset_type=asset_type, aliases=tuple(dict.fromkeys((alias, name, ticker, _normal_alias(alias), _normal_alias(name))))))
    return out


@lru_cache(maxsize=1)
def _load_krx_catalog() -> list[SymbolEntry]:
    entries: list[SymbolEntry] = []
    try:
        from pykrx import stock  # type: ignore
    except Exception:
        return [entry for entry in _manual_stock_entries() if entry.asset_type == "stock_kr"]

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
            compact = _normal_alias(name)
            aliases = tuple(dict.fromkeys((name, compact, code, f"{code}{suffix}")))
            entries.append(SymbolEntry(name=name, ticker=f"{code}{suffix}", asset_type="stock_kr", aliases=aliases))

    known = {entry.ticker for entry in entries}
    for entry in _manual_stock_entries():
        if entry.asset_type == "stock_kr" and entry.ticker not in known:
            entries.append(entry)
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
            aliases = tuple(dict.fromkeys((symbol, base_name, name.split(" - ")[0].strip(), _normal_alias(base_name))))
            entries.append(SymbolEntry(name=base_name, ticker=symbol, asset_type="stock_us", aliases=aliases))

    for entry in _manual_stock_entries():
        if entry.asset_type == "stock_us" and entry.ticker not in seen:
            entries.append(entry)
            seen.add(entry.ticker)
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
                entries.append(SymbolEntry(name=korean_name, ticker=base, asset_type="crypto", aliases=(base, korean_name, english_name, _normal_alias(korean_name))))
        except Exception:
            pass

    for alias, (name, ticker, asset_type) in COMMON_ALIASES.items():
        if asset_type == "crypto" and ticker not in seen:
            seen.add(ticker)
            entries.append(SymbolEntry(name=name, ticker=ticker, asset_type=asset_type, aliases=(alias, name, ticker, _normal_alias(alias))))
    return entries


@lru_cache(maxsize=1)
def _catalogs() -> tuple[list[SymbolEntry], list[SymbolEntry], list[SymbolEntry]]:
    return _load_krx_catalog(), _load_us_catalog(), _load_crypto_catalog()


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    if alias.isascii() and alias.replace("-", "").isalnum():
        return re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE) is not None
    normal_text = _normal_alias(text)
    normal_alias = _normal_alias(alias)
    return bool(normal_alias and normal_alias in normal_text)


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


def _strict_us_tickers(text: str) -> set[str]:
    out: set[str] = set()
    for match in STRICT_TICKER_RE.finditer(text):
        ticker = (match.group(1) or match.group(2) or "").upper().strip()
        if ticker and ticker not in BAD_TICKERS:
            out.add(ticker)
    return out


def _has_us_ticker_context(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in US_TICKER_CONTEXT_WORDS)


def resolve_symbols(text: str, categories: list[str] | None = None, raw_tickers: list[str] | None = None) -> list[ResolvedSymbol]:
    categories = categories or []
    raw_tickers = raw_tickers or []
    result: list[ResolvedSymbol] = []
    seen: set[str] = set()

    lower = text.lower()
    is_crypto_context = "crypto" in categories or any(
        word in lower for word in ["btc", "비트코인", "코인", "온체인", "업비트", "바이낸스", "usdt"]
    )
    strict_tickers = _strict_us_tickers(text)
    us_context = _has_us_ticker_context(text)

    krx_catalog, us_catalog, crypto_catalog = _catalogs()

    for code in KR_CODE_RE.findall(text):
        entry = _find_entry_by_kr_code(code, krx_catalog)
        if entry:
            _append(result, seen, entry)

    for entry in krx_catalog:
        if any(_contains_alias(text, alias) for alias in entry.aliases):
            _append(result, seen, entry)

    for entry in us_catalog:
        name_aliases = [a for a in entry.aliases if a.upper() != entry.ticker]
        if any(_contains_alias(text, alias) for alias in name_aliases):
            _append(result, seen, entry)

    for entry in crypto_catalog:
        if any(_contains_alias(text, alias) for alias in entry.aliases):
            _append(result, seen, entry)

    crypto_tickers = {entry.ticker for entry in crypto_catalog}
    us_by_ticker = {entry.ticker: entry for entry in us_catalog}
    raw_candidates = sorted(set(raw_tickers + list(strict_tickers)))

    if us_context:
        raw_candidates.extend([ticker for ticker in UPPER_TICKER_RE.findall(text) if len(ticker) >= 3])

    for ticker in sorted(set(raw_candidates)):
        ticker = ticker.upper().strip()
        if ticker in BAD_TICKERS or ticker in seen:
            continue
        if ticker in crypto_tickers or is_crypto_context:
            result.append(ResolvedSymbol(name=ticker, ticker=ticker, asset_type="crypto"))
            seen.add(ticker)
        elif ticker in us_by_ticker:
            _append(result, seen, us_by_ticker[ticker])
        elif ticker in strict_tickers and len(ticker) >= 2:
            result.append(ResolvedSymbol(name=ticker, ticker=ticker, asset_type="stock_or_us"))
            seen.add(ticker)

    return result
