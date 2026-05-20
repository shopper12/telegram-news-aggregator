from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
import os

from .summarizer import SummaryItem
from .symbol_resolver import resolve_symbols, ResolvedSymbol
from .web_research import WebImpact, judge_web_impact
from .market_data import build_strategy, fetch_market_overview


DIVIDER = "━━━━━━━━━━━━━━"
SUB_DIVIDER = "──────"
CRYPTO_SECTORS = {"bitcoin", "ethereum", "solana", "xrp", "sui", "defi", "ai_coin", "rwa"}
STOCK_CATEGORIES = {"stock", "korea_stock", "us_stock", "kr_stock"}
CRYPTO_CATEGORIES = {"crypto", "coin"}
IMPORTANT_THRESHOLD = 7
MAX_IMPORTANT_PER_ASSET = 2
MAX_SYMBOLS_PER_ASSET = 3

EVENT_WORDS = ["단독", "속보", "수주", "계약", "공급", "납품", "승인", "허가", "공시", "상장", "인수", "합병", "실적", "어닝", "가이던스"]
MACRO_WORDS = ["fomc", "fed", "금리", "환율", "cpi", "ppi", "고용", "실업률", "국채", "10년물", "달러", "dxy", "유가", "관세"]
THEME_WORDS = ["관련주", "수혜", "기대", "전망", "관심", "부각", "테마"]
PRICED_IN_WORDS = ["급등", "상한가", "폭등", "신고가", "장대양봉", "이미 반영", "선반영"]
RISK_WORDS = ["급락", "악재", "제재", "조사", "소송", "유상증자", "상장폐지", "거래정지", "부도", "파산"]
NOISE_WORDS = ["리딩", "무료방", "입장", "유료", "회원", "추천방", "수익인증", "이벤트 참여"]


@dataclass(frozen=True)
class ScoredNews:
    summary: SummaryItem
    score: int
    reasons: list[str]
    symbols: list[ResolvedSymbol]
    impact: WebImpact


def _category_asset(summary: SummaryItem) -> str:
    cats = {c.lower() for c in summary.categories}
    if cats & CRYPTO_CATEGORIES:
        return "crypto"
    if cats & STOCK_CATEGORIES:
        return "stock"
    sectors = set(summary.sectors)
    text = f"{summary.title} {summary.body}".lower()
    if sectors & CRYPTO_SECTORS:
        return "crypto"
    if any(word in text for word in ["btc", "비트코인", "코인", "업비트", "바이낸스", "온체인"]):
        return "crypto"
    return "stock"


def _symbols_for_asset(summary: SummaryItem, asset_type: str) -> list[ResolvedSymbol]:
    symbols = resolve_symbols(f"{summary.title} {summary.body}", summary.categories, summary.tickers)
    if asset_type == "crypto":
        return [s for s in symbols if s.asset_type == "crypto"]
    return [s for s in symbols if s.asset_type != "crypto"]


def _impact_icon(level: str) -> str:
    if level == "높음":
        return "🔥"
    if level == "중간":
        return "🟠"
    if level == "낮음":
        return "⚪"
    return "▫️"


def _query_for_impact(summary: SummaryItem, symbols: list[ResolvedSymbol]) -> str:
    if symbols:
        first = symbols[0]
        return f"{first.name} {first.ticker} {summary.title[:50]}"
    return summary.title[:80]


def _score_news(summary: SummaryItem, asset_type: str, impact_cache: dict[str, WebImpact]) -> ScoredNews:
    text = f"{summary.title} {summary.body}".lower()
    symbols = _symbols_for_asset(summary, asset_type)
    query = _query_for_impact(summary, symbols)
    if query not in impact_cache:
        impact_cache[query] = judge_web_impact(query)
    impact = impact_cache[query]

    score = 4
    reasons: list[str] = []
    if any(word in text for word in EVENT_WORDS):
        score += 3; reasons.append("이벤트")
    if summary.repeat_count >= 2:
        score += 1; reasons.append(f"반복{summary.repeat_count}")
    if any(word in text for word in MACRO_WORDS):
        score += 2; reasons.append("매크로")
    if any(word in text for word in RISK_WORDS):
        score += 2; reasons.append("리스크")
    if symbols:
        score += 1; reasons.append("종목명확")
    if impact.impact_level == "높음":
        score += 2; reasons.append("외부확인강")
    elif impact.impact_level == "중간":
        score += 1; reasons.append("외부확인중")
    if any(word in text for word in THEME_WORDS):
        score -= 1; reasons.append("테마감점")
    if any(word in text for word in PRICED_IN_WORDS):
        score -= 1; reasons.append("선반영감점")
    if any(word in text for word in NOISE_WORDS):
        score -= 3; reasons.append("홍보감점")
    score = max(1, min(10, score))
    if not reasons:
        reasons.append("정보성")
    return ScoredNews(summary=summary, score=score, reasons=reasons, symbols=symbols, impact=impact)


def _score_asset_news(summaries: list[SummaryItem], asset_type: str, impact_cache: dict[str, WebImpact]) -> list[ScoredNews]:
    scored = [_score_news(summary, asset_type, impact_cache) for summary in summaries]
    important = [item for item in scored if item.score >= IMPORTANT_THRESHOLD]
    return sorted(important, key=lambda x: (x.score, x.summary.repeat_count), reverse=True)[:MAX_IMPORTANT_PER_ASSET]


def _split_by_asset(summaries: list[SummaryItem]) -> tuple[list[SummaryItem], list[SummaryItem]]:
    stock, crypto = [], []
    for summary in summaries:
        (crypto if _category_asset(summary) == "crypto" else stock).append(summary)
    return stock, crypto


def _sector_summary(items: list[ScoredNews]) -> str:
    sectors = Counter()
    for item in items:
        sectors.update(item.summary.sectors)
    if not sectors:
        return "불명확"
    return ", ".join([f"{name}({count})" for name, count in sectors.most_common(4)])


def _market_direction(items: list[ScoredNews]) -> str:
    if not items:
        return "중요 뉴스 부족, 관망"
    reason_counter = Counter()
    for item in items:
        reason_counter.update(item.reasons)
    sector = _sector_summary(items).split(",")[0]
    reason = reason_counter.most_common(1)[0][0] if reason_counter else "정보성"
    return f"{sector} 중심. 근거={reason}"


def _unique_symbols(items: list[ScoredNews], limit: int = MAX_SYMBOLS_PER_ASSET) -> list[tuple[ResolvedSymbol, ScoredNews]]:
    result: list[tuple[ResolvedSymbol, ScoredNews]] = []
    seen: set[str] = set()
    for item in items:
        for symbol in item.symbols:
            if symbol.ticker in seen:
                continue
            seen.add(symbol.ticker)
            result.append((symbol, item))
            if len(result) >= limit:
                return result
    return result


def _asset_type_for_strategy(symbol: ResolvedSymbol) -> str:
    return "crypto" if symbol.asset_type == "crypto" else "stock"


def _quote_url(symbol: ResolvedSymbol) -> str:
    ticker = symbol.ticker.upper()
    if symbol.asset_type == "crypto":
        return f"https://www.binance.com/en/trade/{ticker}_USDT"
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return f"https://finance.naver.com/item/main.naver?code={ticker.split('.')[0]}"
    return f"https://finance.yahoo.com/quote/{ticker}"


def _format_price(symbol: ResolvedSymbol, item: ScoredNews) -> tuple[str, str]:
    strategy = build_strategy(symbol.ticker, _asset_type_for_strategy(symbol), item.score, item.summary.risk)
    quote = strategy.quote
    if quote and quote.price is not None:
        change = f" {quote.change_pct:+.2f}%" if quote.change_pct is not None else ""
        price = f"{quote.price:,.2f}{change}"
    else:
        price = "가격확인불가"
    short_strategy = f"진입 {strategy.entry} / 손절 {strategy.stop} / 목표 {strategy.target}"
    return price, short_strategy


def _render_asset_section(lines: list[str], title: str, items: list[ScoredNews]) -> None:
    lines.append(title)
    lines.append(f"시황: {_market_direction(items)}")
    lines.append(f"섹터: {_sector_summary(items)}")
    if items:
        lines.append("뉴스: " + " / ".join([f"[{x.score}] {x.summary.title[:45]}" for x in items[:2]]))
    else:
        lines.append("뉴스: 중요도 통과 없음")

    symbols = _unique_symbols(items)
    if symbols:
        lines.append("종목:")
        for idx, (symbol, item) in enumerate(symbols, start=1):
            price, strategy = _format_price(symbol, item)
            lines.append(f"{idx}) {symbol.name}({symbol.ticker}) {price}")
            lines.append(_quote_url(symbol))
            lines.append(f"   {strategy}")
    else:
        lines.append("종목: 직접 언급 종목 없음")
    lines.append("")


def _special_header(kind: str) -> str:
    if kind == "preopen_0850":
        return "🌅 08:50 개장 전 브리핑"
    if kind == "afterclose_1530":
        return "🏁 15:30 장후 브리핑"
    return "📰 뉴스 브리핑"


def _special_note(kind: str, stock_items: list[ScoredNews], crypto_items: list[ScoredNews]) -> list[str]:
    if kind == "preopen_0850":
        return ["관점: 개장 전은 뉴스보다 장초반 거래대금 확인 우선", f"주요 섹터: {_sector_summary(stock_items)}"]
    if kind == "afterclose_1530":
        return ["관점: 장후는 당일 강세 섹터 지속성·시간외 뉴스 확인", f"주요 섹터: {_sector_summary(stock_items)}"]
    return []


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    impact_cache: dict[str, WebImpact] = {}
    stock_raw, crypto_raw = _split_by_asset(summaries)
    stock_items = _score_asset_news(stock_raw, "stock", impact_cache)
    crypto_items = _score_asset_news(crypto_raw, "crypto", impact_cache)
    total = len(stock_items) + len(crypto_items)

    lines: list[str] = []
    lines.append(_special_header(kind))
    lines.append(DIVIDER)
    lines.append(f"{now:%m/%d %H:%M KST} | 최근 {hours}h | 중요뉴스 {total}")
    overview = fetch_market_overview()
    if overview:
        lines.append("시장: " + " / ".join(overview[:5]))
    lines.extend(_special_note(kind, stock_items, crypto_items))
    lines.append("")
    _render_asset_section(lines, "📈 주식", stock_items)
    _render_asset_section(lines, "🪙 코인", crypto_items)
    lines.append("기준: 이벤트·외부확인·종목명확성 우선. 홍보성/선반영 제외.")
    return "\n".join(lines)
