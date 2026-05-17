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
    "방산": ["방산", "무기", "수출계약", "k9", "천무", "드론"],
    "조선": ["조선", "lng선", "선박", "수주잔고", "해양플랜트"],
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

TICKER_RE = re.compile(r"\b[A-Z]{2,10}\b")
BAD_TICKERS = {"AI", "SK", "KV", "ETF", "CEO", "SEC", "FED", "FOMC", "GDP", "CPI", "KOSPI", "KOSDAQ"}


@dataclass(frozen=True)
class ExtractedSignal:
    sectors: list[str]
    keywords: list[str]
    tickers: list[str]
    importance_score: int


def extract_signals(text: str, repeat_count: int = 1) -> ExtractedSignal:
    lower = text.lower()
    sector_hits: list[str] = []
    keyword_hits: list[str] = []

    for sector, words in {**KOREAN_STOCK_KEYWORDS, **CRYPTO_KEYWORDS}.items():
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
    if any(w in lower for w in ["단독", "속보", "수주", "계약", "승인", "상장", "공급", "납품"]):
        score += 3

    return ExtractedSignal(
        sectors=sectors,
        keywords=keywords,
        tickers=tickers,
        importance_score=score,
    )


def top_terms(items: list[str], limit: int = 20) -> list[tuple[str, int]]:
    counter = Counter(items)
    return counter.most_common(limit)
