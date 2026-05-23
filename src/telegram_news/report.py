from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
import os

from .summarizer import SummaryItem
from .symbol_resolver import resolve_symbols, ResolvedSymbol
from .web_research import WebImpact, judge_web_impact
from .market_data import fetch_market_overview


DIVIDER = "━━━━━━━━━━━━━━"
BLOCK_CATEGORIES = {"crypto", "coin"}
BLOCK_SECTORS = {"bitcoin", "ethereum", "solana", "xrp", "sui", "defi", "ai_coin", "rwa"}
BLOCK_WORDS = ["btc", "eth", "비트코인", "이더리움", "코인", "업비트", "바이낸스", "온체인"]
STOCK_CATEGORIES = {"stock", "korea_stock", "us_stock", "kr_stock"}
EVENT_WORDS = ["단독", "속보", "수주", "계약", "공급", "납품", "승인", "허가", "공시", "상장", "인수", "합병", "실적", "가이던스"]
MACRO_WORDS = ["fomc", "fed", "금리", "환율", "cpi", "ppi", "고용", "국채", "달러", "유가", "관세"]
RISK_WORDS = ["급락", "악재", "제재", "조사", "소송", "유상증자", "거래정지", "부도", "파산"]
SOFT_WORDS = ["관련주", "수혜", "기대", "전망", "관심", "부각", "테마"]
MAX_NEWS = 5
BASE_SCORE = 7


@dataclass(frozen=True)
class Pick:
    item: SummaryItem
    score: int
    reasons: list[str]
    symbols: list[ResolvedSymbol]
    impact: WebImpact


def _txt(item: SummaryItem) -> str:
    return f"{item.title} {item.body}"


def _blocked(item: SummaryItem) -> bool:
    cats = {c.lower() for c in item.categories}
    if cats & BLOCK_CATEGORIES:
        return True
    if set(item.sectors) & BLOCK_SECTORS:
        return True
    low = _txt(item).lower()
    return any(w in low for w in BLOCK_WORDS)


def _stock_candidate(item: SummaryItem) -> bool:
    if _blocked(item):
        return False
    cats = {c.lower() for c in item.categories}
    return bool(cats & STOCK_CATEGORIES) or not cats


def _quote_url(sym: ResolvedSymbol) -> str:
    ticker = sym.ticker.upper()
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return f"https://finance.naver.com/item/main.naver?code={ticker.split('.')[0]}"
    return f"https://finance.yahoo.com/quote/{ticker}"


def _direct_symbols(item: SummaryItem) -> list[ResolvedSymbol]:
    text = _txt(item)
    low = text.lower()
    out: list[ResolvedSymbol] = []
    seen: set[str] = set()
    for sym in resolve_symbols(text, item.categories, item.tickers):
        if sym.asset_type == "crypto":
            continue
        base = sym.ticker.upper().replace(".KS", "").replace(".KQ", "")
        direct = sym.name.lower() in low or sym.ticker.lower() in low or base in text
        if direct and sym.ticker not in seen:
            seen.add(sym.ticker)
            out.append(sym)
    return out[:3]


def _score(item: SummaryItem, cache: dict[str, WebImpact]) -> Pick:
    low = _txt(item).lower()
    syms = _direct_symbols(item)
    query = f"{syms[0].name} {syms[0].ticker} {item.title[:50]}" if syms else item.title[:80]
    if query not in cache:
        cache[query] = judge_web_impact(query)
    impact = cache[query]
    score = 4
    reasons: list[str] = []
    if any(w in low for w in EVENT_WORDS):
        score += 3
        reasons.append("이벤트")
    if any(w in low for w in MACRO_WORDS):
        score += 2
        reasons.append("거시")
    if any(w in low for w in RISK_WORDS):
        score += 2
        reasons.append("리스크")
    if syms:
        score += 1
        reasons.append("직접종목")
    if impact.impact_level == "높음":
        score += 2
        reasons.append("외부확인강")
    elif impact.impact_level == "중간":
        score += 1
        reasons.append("외부확인중")
    if item.repeat_count >= 2:
        score += 1
        reasons.append(f"반복{item.repeat_count}")
    if any(w in low for w in SOFT_WORDS):
        score -= 1
        reasons.append("테마감점")
    return Pick(item, max(1, min(10, score)), reasons or ["정보성"], syms, impact)


def _select(items: list[SummaryItem]) -> tuple[list[Pick], int, int, int, str]:
    stock = [x for x in items if _stock_candidate(x)]
    blocked = len([x for x in items if _blocked(x)])
    cache: dict[str, WebImpact] = {}
    scored = [_score(x, cache) for x in stock]
    strong = [x for x in scored if x.score >= BASE_SCORE]
    if not strong:
        threshold = 6
        rule = "완화: 중요 뉴스 부족"
    elif len(strong) > MAX_NEWS:
        threshold = 8
        rule = "강화: 후보 과다"
    else:
        threshold = BASE_SCORE
        rule = "기본"
    picks = sorted([x for x in scored if x.score >= threshold], key=lambda x: x.score, reverse=True)[:MAX_NEWS]
    return picks, len(stock), blocked, threshold, rule


def _sectors(picks: list[Pick]) -> str:
    counter = Counter()
    for pick in picks:
        counter.update(pick.item.sectors)
    return ", ".join(f"{k}({v})" for k, v in counter.most_common(4)) or "불명확"


def _view(picks: list[Pick]) -> str:
    if not picks:
        return "선별 기준 통과 뉴스 없음."
    counter = Counter()
    for pick in picks:
        counter.update(pick.reasons)
    return f"{_sectors(picks).split(',')[0]} 중심. 핵심={counter.most_common(1)[0][0]}."


def _links(symbols: list[ResolvedSymbol]) -> str:
    if not symbols:
        return "직접 언급 종목 없음"
    return ", ".join(f"{s.name}({s.ticker}) {_quote_url(s)}" for s in symbols)


def _short(text: str, limit: int) -> str:
    one = " ".join(text.replace("\n", " ").split())
    return one if len(one) <= limit else one[:limit - 1] + "…"


def _header(kind: str) -> str:
    if kind == "preopen_0850":
        return "🌅 08:50 개장 전 주식 브리핑"
    if kind == "afterclose_1530":
        return "🏁 15:30 장후 주식 브리핑"
    return "📰 주식 뉴스 브리핑"


def _overview() -> str:
    items = [x for x in fetch_market_overview() if not x.startswith(("BTC", "ETH"))]
    return " / ".join(items[:5]) if items else "시장 데이터 확인 실패"


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    picks, stock_count, blocked, threshold, rule = _select(summaries)
    lines: list[str] = [
        _header(kind),
        DIVIDER,
        f"{now:%m/%d %H:%M KST} | 최근 {hours}h | 선별 {len(picks)}건 | 기준 {threshold}점",
        f"시장: {_overview()}",
        f"시황: {_view(picks)}",
        f"주요 섹터: {_sectors(picks)}",
        "",
    ]
    if not picks:
        lines.append("주요 뉴스: 기준 통과 없음")
    else:
        lines.append("📌 주요 뉴스")
        for i, pick in enumerate(picks, 1):
            lines.append(f"{i}) [{pick.score}] {_short(pick.item.title, 70)}")
            lines.append(f"   핵심: {_short(pick.item.body, 120)}")
            lines.append(f"   영향: {', '.join(pick.reasons[:3])} / 섹터 {', '.join(pick.item.sectors[:3]) or '불명확'}")
            lines.append(f"   관련: {_links(pick.symbols)}")
    lines.append("")
    lines.append(f"검증: 기준={rule} · 제외 {blocked}건 · 후보 {stock_count}건 · 직접언급 종목만")
    report = "\n".join(lines)
    return report[:2180] + "\n… 이하 생략" if len(report) > 2200 else report
