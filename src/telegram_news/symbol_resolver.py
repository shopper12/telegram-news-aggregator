from __future__ import annotations

import re
from dataclasses import dataclass


KOREAN_NAME_TO_TICKER = {
    "삼성전자": "005930.KS",
    "SK하이닉스": "000660.KS",
    "현대차": "005380.KS",
    "기아": "000270.KS",
    "한화에어로스페이스": "012450.KS",
    "HD현대일렉트릭": "267260.KS",
    "LS ELECTRIC": "010120.KS",
    "두산에너빌리티": "034020.KS",
    "우리기술": "032820.KQ",
    "서전기전": "189860.KQ",
    "파워넷": "037030.KQ",
    "비나텍": "126340.KQ",
    "성호전자": "043260.KQ",
}

CRYPTO_NAME_TO_TICKER = {
    "비트코인": "BTC",
    "bitcoin": "BTC",
    "btc": "BTC",
    "이더리움": "ETH",
    "ethereum": "ETH",
    "eth": "ETH",
    "솔라나": "SOL",
    "solana": "SOL",
    "sol": "SOL",
    "리플": "XRP",
    "ripple": "XRP",
    "xrp": "XRP",
    "수이": "SUI",
    "sui": "SUI",
    "온도": "ONDO",
    "ondo": "ONDO",
    "virtual": "VIRTUAL",
    "렌더": "RENDER",
    "render": "RENDER",
    "near": "NEAR",
    "tao": "TAO",
    "fet": "FET",
}

BAD_TICKERS = {"AI", "SK", "KV", "ETF", "CEO", "SEC", "FED", "FOMC", "GDP", "CPI", "KOSPI", "KOSDAQ"}
UPPER_TICKER_RE = re.compile(r"\b[A-Z]{2,12}\b")


@dataclass(frozen=True)
class ResolvedSymbol:
    name: str
    ticker: str
    asset_type: str


def resolve_symbols(text: str, categories: list[str] | None = None, raw_tickers: list[str] | None = None) -> list[ResolvedSymbol]:
    lower = text.lower()
    categories = categories or []
    raw_tickers = raw_tickers or []
    result: list[ResolvedSymbol] = []
    seen: set[str] = set()

    is_crypto_context = "crypto" in categories or any(word in lower for word in ["btc", "비트코인", "코인", "온체인", "업비트", "바이낸스"])

    for name, ticker in KOREAN_NAME_TO_TICKER.items():
        if name.lower() in lower and ticker not in seen:
            result.append(ResolvedSymbol(name=name, ticker=ticker, asset_type="stock"))
            seen.add(ticker)

    for name, ticker in CRYPTO_NAME_TO_TICKER.items():
        if name.lower() in lower and ticker not in seen:
            result.append(ResolvedSymbol(name=name.upper() if name.islower() else name, ticker=ticker, asset_type="crypto"))
            seen.add(ticker)

    for ticker in sorted(set(raw_tickers + UPPER_TICKER_RE.findall(text))):
        if ticker in BAD_TICKERS or ticker in seen:
            continue
        asset_type = "crypto" if is_crypto_context or ticker in set(CRYPTO_NAME_TO_TICKER.values()) else "stock_or_us"
        result.append(ResolvedSymbol(name=ticker, ticker=ticker, asset_type=asset_type))
        seen.add(ticker)

    return result
