from __future__ import annotations

import re
from dataclasses import dataclass
from collections import Counter


KOREAN_STOCK_KEYWORDS = {
    "반도체": ["반도체", "hbm", "dram", "낸드", "파운드리", "삼성전자", "sk하이닉스", "메모리", "tsmc"],
    "AI인프라": ["ai 추론", "ai 인프라", "데이터센터", "gpu", "npu", "hbm", "서버", "인공지능 반도체"],
    "원전": ["원전", "smr", "체코", "웨스팅하우스", "한수원", "원자로", "원전수주"],
    "전력기기": ["전력기기", "변압기", "전선", "송전", "배전", "hvdc", "전력망"],
    "로봇": ["로봇", "휴머노이드", "피지컬 ai", "감속기", "액추에이터"],
    "2차전지": ["2차전지", "배터리", "양극재", "음극재", "전해액", "리튬"],
    "조선": ["조선", "lng선", "선박", "수주잔고", "해양플랜트"],
    "바이오": ["임상", "fda", "신약", "임상3상", "바이오시밀러", "셀트리온", "삼성바이오", "허가", "cda", "nda", "anda", "품목허가"],
    "양자": ["양자컴", "양자암호", "ibm q", "구글 퀀텀", "양자컴퓨터", "퀀텀"],
    "미국빅테크": ["엔비디아", "마이크로소프트", "애플", "알파벳", "메타", "아마존", "테슬라", "nvda", "msft", "aapl", "googl", "amzn", "tsla"],
}

US_STOCK_KEYWORDS = {
    "미국빅테크": ["엔비디아", "마이크로소프트", "애플", "알파벳", "메타", "아마존", "테슬라", "nvda", "msft", "aapl", "googl", "meta", "amzn", "tsla"],
    "반도체": ["nvidia", "nvda", "amd", "broadcom", "avgo", "micron", "mu", "semiconductor", "gpu", "hbm"],
    "AI인프라": ["ai", "artificial intelligence", "data center", "datacenter", "gpu", "server", "oracle", "orcl"],
    "양자": ["quantum", "quantum computing", "ibm q", "google quantum", "ionq", "rigetti"],
    "바이오": ["fda", "clinical trial", "phase 3", "drug approval", "biotech", "eli lilly", "novo nordisk", "nda", "anda"],
}

CRYPTO_KEYWORDS = {
    "bitcoin": ["btc", "비트코인", "bitcoin"],
    "ethereum": ["eth", "이더리움", "ethereum"],
    "solana": ["sol", "솔라나", "solana"],
    "xrp": ["xrp", "리플", "ripple"],
    "sui": ["sui"],
    "etf": ["현물 etf", "spot etf", "자금유입", "순유입", "순유출"],
    "defi": ["defi", "디파이", "tvl", "스테이킹"],
    "ai_coin": ["ai 코인", "ai agent", "에이전트 코인", "virtual", "tao", "fet", "render", "near", "grass"],
    "rwa": ["rwa", "토큰화", "real world asset", "ondo"],
}

BREAKING_WORDS = ["단독", "속보"]
CONTRACT_WORDS = ["수주", "계약"]
ACTION_WORDS = ["승인", "상장", "공급", "납품"]
TICKER_RE = re.compile(r"\b[A-Z]{2,10}\b")
BAD_TICKERS = {"AI", "SK", "KV", "ETF", "CEO", "SEC", "FED", "FOMC", "GDP", "CPI", "KOSPI", "KOSDAQ"}


@dataclass(frozen=True)
class ExtractedSignal:
    sectors: list[str]
    keywords: list[str]
    tickers: list[str]
    importance_score: int


def _merge_keyword_maps(*maps: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for keyword_map in maps:
        for sector, words in keyword_map.items():
            bucket = merged.setdefault(sector, [])
            seen = {word.lower() for word in bucket}
            for word in words:
                key = word.lower()
                if key not in seen:
                    bucket.append(word)
                    seen.add(key)
    return merged


def _keyword_maps_for_market(market_type: str) -> dict[str, list[str]]:
    normalized = (market_type or "KR").upper()
    if normalized == "CRYPTO":
        return CRYPTO_KEYWORDS
    if normalized == "US":
        return US_STOCK_KEYWORDS
    return _merge_keyword_maps(KOREAN_STOCK_KEYWORDS, US_STOCK_KEYWORDS)


def market_type_from_categories(categories: list[str] | tuple[str, ...] | set[str] | None) -> str:
    cats = {str(c).lower() for c in (categories or [])}
    if cats & {"crypto", "coin"}:
        return "CRYPTO"
    if "us_stock" in cats:
        return "US"
    return "KR"


def extract_signals(text: str, repeat_count: int = 1, market_type: str = "KR") -> ExtractedSignal:
    lower = text.lower()
    sector_hits: list[str] = []
    keyword_hits: list[str] = []

    for sector, words in _keyword_maps_for_market(market_type).items():
        for word in words:
            if word.lower() in lower:
                sector_hits.append(sector)
                keyword_hits.append(word)

    raw_tickers = sorted(set(TICKER_RE.findall(text)))
    tickers = [ticker for ticker in raw_tickers if ticker not in BAD_TICKERS]
    sectors = sorted(set(sector_hits))
    keywords = sorted(set(keyword_hits), key=lambda x: x.lower())

    score = 0
    score += repeat_count * 2
    score += len(sectors) * 2
    score += min(len(tickers), 5)
    if any(w.lower() in lower for w in BREAKING_WORDS):
        score += 8
    if any(w.lower() in lower for w in CONTRACT_WORDS):
        score += 7
    if any(w.lower() in lower for w in ACTION_WORDS):
        score += 6
    if len(sectors) >= 3:
        score -= 2
    if repeat_count >= 3:
        score -= 3

    return ExtractedSignal(
        sectors=sectors,
        keywords=keywords,
        tickers=tickers,
        importance_score=max(0, score),
    )


def top_terms(items: list[str], limit: int = 20) -> list[tuple[str, int]]:
    counter = Counter(items)
    return counter.most_common(limit)
