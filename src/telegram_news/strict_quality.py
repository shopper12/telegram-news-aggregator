from __future__ import annotations

import re
from collections import defaultdict

from .noise_patterns import LOW_VALUE_WORDS, ADVISORY_WORDS

# 뉴스 제목 알림용 중요도 게이트.
# Gemini 장애/쿼터 초과 시에도 아래 로컬 규칙이 1차 판단 엔진으로 동작한다.
CORE_TYPES = {"공시/확정", "이벤트", "실적", "리스크", "거시"}
TRADE_CORE_TYPES = {"공시/확정", "이벤트", "실적"}
WATCH_TYPES = {"가격반응", "테마", "정보"}
LOW_VALUE_TYPES = {"가격반응", "테마", "정보"}
MATERIALITY_THRESHOLD = 68
WATCH_THRESHOLD = 72
MAX_NEWS = 12
MAX_PER_SECTOR = 3

LOCAL_AI_RUBRIC_PROMPT = """
로컬AI 중요도 기준:
- 공시·수주·계약·실적·승인·허가·소송·규제·정책·금리·환율은 우선 검토한다.
- 금액, 비율, 매출, 영업이익, 수주액, 허가일, 공시 등 확정 근거가 있으면 가점한다.
- 관련주, 수혜, 전망, 기대, 단순 급등, 상한가, 레딧/게시물 분석, ETF 단순 신규상장은 감점한다.
- 외국인/기관 수급처럼 시장 흐름 뉴스는 종목별 직접 재료가 아니면 낮게 본다.
- 종목명은 뉴스에 직접 나온 경우만 인정하고, 불확실하면 관련 종목을 숨긴다.
""".strip()

CONFIRMATION_WORDS = [
    "공시", "잠정", "전자공시", "거래소", "금감원", "수주", "계약", "공급", "납품",
    "승인", "허가", "품목허가", "fda", "임상3상", "실적", "가이던스", "매출", "영업이익",
    "eps", "배당", "자사주", "증자", "소송", "제재", "거래정지",
]
CONTRACT_WORDS = ["수주", "계약"]
MARKET_WIDE_WORDS = [
    "fomc", "fed", "연준", "한은", "금리", "환율", "cpi", "ppi", "고용", "국채", "달러", "유가",
    "관세", "수출규제", "반도체 수출", "itar", "코스피", "코스닥", "나스닥", "엔비디아",
]
BROAD_FLOW_WORDS = ["외국인", "기관", "개인", "순매수", "순매도", "코스피 팔고", "코스닥 담았다", "수급"]
THEME_WORDS = ["관련주", "수혜", "기대", "전망", "관심", "부각", "테마", "가능성"]
STRONG_THEME_PENALTY_WORDS = ["관련주", "수혜", "전망"]
PRICE_WORDS = ["급등", "상한가", "폭등", "신고가", "장대양봉", "강세", "상승세"]
NUMBER_EVIDENCE_RE = re.compile(r"\d+(?:\.\d+)?\s*(조|억|만|%|달러|원|억원|조원|bp|톤)", re.IGNORECASE)


def _has_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _symbols_have_market_data(cluster) -> bool:
    """시장 데이터 보너스는 실패해도 게이트 전체를 죽이지 않는다."""
    try:
        from .market_data import fetch_quote

        best = cluster.best()
        symbols = list(cluster.symbols()) if hasattr(cluster, "symbols") else list(getattr(best, "symbols", []) or [])
        for sym in symbols[:3]:
            quote = fetch_quote(sym.ticker, getattr(sym, "asset_type", "stock"))
            if quote.price is not None or quote.change_pct is not None or quote.turnover is not None:
                return True
    except Exception:
        return False
    return False


def _local_ai_delta(cluster) -> int:
    best = cluster.best()
    item = best.item
    text = f"{item.title} {item.body}"
    lower = text.lower()
    delta = 0

    has_confirmation = _has_any(lower, CONFIRMATION_WORDS)
    has_market_wide = _has_any(lower, MARKET_WIDE_WORDS)
    has_number = bool(NUMBER_EVIDENCE_RE.search(text))
    low_value = _has_any(lower, LOW_VALUE_WORDS) or ("etf" in lower and "신규 상장" in lower)
    reddit_analysis = "레딧" in lower or "reddit" in lower or "게시물 분석" in lower
    broad_flow = _has_any(lower, BROAD_FLOW_WORDS) and ("코스피" in lower or "코스닥" in lower)

    if best.news_type in TRADE_CORE_TYPES:
        if has_confirmation or has_number:
            delta += 14
        elif has_market_wide:
            delta += 7
    elif best.news_type in {"리스크", "거시"}:
        if has_market_wide or has_number:
            delta += 9
    elif best.news_type in WATCH_TYPES:
        if best.impact.impact_level == "높음" and has_confirmation:
            delta += 6
        if has_number or has_market_wide:
            delta += 4
        if _has_any(text, ADVISORY_WORDS):
            delta -= 30

    if getattr(item, "gemini_news_type", "") in TRADE_CORE_TYPES:
        delta += 5
    if getattr(item, "gemini_impact", "") == "높음":
        delta += 4
    elif getattr(item, "gemini_impact", "") == "낮음" and best.news_type in WATCH_TYPES:
        delta -= 4

    if low_value or reddit_analysis:
        delta -= 28
    if broad_flow and best.news_type not in {"거시", "리스크"}:
        delta -= 12
    if _has_any(lower, THEME_WORDS):
        delta -= 10
    if _has_any(lower, PRICE_WORDS) and not has_confirmation:
        delta -= 12
    if len(cluster.sectors()) >= 3:
        delta -= 4
    if not best.symbols and best.news_type not in {"거시", "리스크"}:
        delta -= 4

    return delta


def _is_low_value_cluster(cluster) -> bool:
    best = cluster.best()
    text = f"{best.item.title} {best.item.body}".lower()
    return (
        _has_any(text, LOW_VALUE_WORDS)
        or "레딧" in text
        or "reddit" in text
        or ("etf" in text and "신규 상장" in text)
    )


def _has_watch_support(cluster, score: int) -> bool:
    best = cluster.best()
    text = f"{best.item.title} {best.item.body}".lower()
    has_number = bool(NUMBER_EVIDENCE_RE.search(text))
    has_market_wide = _has_any(text, MARKET_WIDE_WORDS)
    has_confirmation = _has_any(text, CONFIRMATION_WORDS)
    repeated = cluster.channel_count() >= 2 or len(cluster.items) >= 2
    high_impact = best.impact.impact_level == "높음" or getattr(best.item, "gemini_impact", "") == "높음"
    return bool(best.symbols or repeated or has_number or has_market_wide or has_confirmation or (high_impact and score >= WATCH_THRESHOLD + 4))


def materiality_score(cluster) -> int:
    best = cluster.best()
    text = f"{best.item.title} {best.item.body}".lower()
    score = cluster.score()

    if best.news_type in TRADE_CORE_TYPES:
        score += 20
    elif best.news_type in {"리스크", "거시"}:
        score += 12
    elif best.news_type in WATCH_TYPES:
        score -= 6

    if best.symbols:
        score += 8
        if _symbols_have_market_data(cluster):
            score += 10
    elif best.news_type not in {"거시", "리스크"}:
        score -= 4

    if cluster.channel_count() >= 3:
        score += 8
    elif cluster.channel_count() == 2:
        score += 5
    if len(cluster.items) >= 2:
        score += 4

    if _has_any(text, CONTRACT_WORDS):
        score += 15

    if best.impact.impact_level == "높음":
        score += 8
    elif best.impact.impact_level == "중간":
        score += 4
    elif best.impact.impact_level == "확인부족" and best.news_type not in {"공시/확정", "이벤트", "거시", "리스크"}:
        score -= 4

    if any(word in text for word in STRONG_THEME_PENALTY_WORDS):
        score -= 15
    elif any(word in text for word in THEME_WORDS):
        score -= 10

    if any(word in text for word in PRICE_WORDS) and best.news_type != "공시/확정":
        score -= 8

    if not cluster.sectors() and best.news_type not in {"거시", "리스크"}:
        score -= 4

    score += _local_ai_delta(cluster)
    return max(1, min(100, score))


def materiality_grade(cluster) -> str:
    score = materiality_score(cluster)
    if score >= 90:
        return "A"
    if score >= 80:
        return "B+"
    if score >= MATERIALITY_THRESHOLD:
        return "B"
    return "C"


def _primary_sector(cluster) -> str:
    sectors = cluster.sectors()
    return sectors[0] if sectors else "__none__"


def _cap_by_sector(clusters: list) -> list:
    kept: list = []
    counts: dict[str, int] = defaultdict(int)
    for cluster in sorted(clusters, key=lambda c: (materiality_score(c), c.score()), reverse=True):
        sector = _primary_sector(cluster)
        if sector != "__none__" and counts[sector] >= MAX_PER_SECTOR:
            continue
        kept.append(cluster)
        counts[sector] += 1
        if len(kept) >= MAX_NEWS:
            break
    return kept


def strict_filter(clusters):
    candidates = []
    for cluster in clusters:
        best = cluster.best()
        score = materiality_score(cluster)
        low_value = _is_low_value_cluster(cluster)

        # 저가치 뉴스는 중간 점수로 통과시키지 않는다. 외부확인 강한 핵심 뉴스일 때만 예외.
        if low_value and not (score >= 88 and best.news_type in TRADE_CORE_TYPES and best.impact.impact_level == "높음"):
            continue

        if best.news_type in CORE_TYPES and score >= MATERIALITY_THRESHOLD:
            candidates.append(cluster)
            continue

        if best.news_type in WATCH_TYPES:
            if _has_watch_support(cluster, score) and score >= WATCH_THRESHOLD:
                candidates.append(cluster)

    return _cap_by_sector(candidates)
