from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter

from .summarizer import SummaryItem
from .symbol_resolver import resolve_symbols, ResolvedSymbol
from .web_research import WebImpact, judge_web_impact
from .market_data import build_strategy, fetch_market_overview


DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
SUB_DIVIDER = "──────────────"
CRYPTO_SECTORS = {"bitcoin", "ethereum", "solana", "xrp", "sui", "defi", "ai_coin", "rwa"}
STOCK_CATEGORIES = {"stock", "korea_stock", "us_stock", "kr_stock"}
CRYPTO_CATEGORIES = {"crypto", "coin"}
IMPORTANT_THRESHOLD = 7
MAX_IMPORTANT_PER_ASSET = 3
MAX_SYMBOLS_PER_ASSET = 5

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
        score += 3
        reasons.append("실제 이벤트성 뉴스")
    if summary.repeat_count >= 2:
        score += 2
        reasons.append(f"복수 채널 반복 {summary.repeat_count}회")
    if any(word in text for word in MACRO_WORDS):
        score += 2
        reasons.append("매크로 변수 포함")
    if any(word in text for word in RISK_WORDS):
        score += 2
        reasons.append("리스크 이벤트 포함")
    if symbols:
        score += 1
        reasons.append("관련 종목/티커 명확")
    if impact.impact_level == "높음":
        score += 2
        reasons.append("외부 뉴스 확인 강함")
    elif impact.impact_level == "중간":
        score += 1
        reasons.append("외부 뉴스 확인 중간")
    if any(word in text for word in THEME_WORDS):
        score -= 1
        reasons.append("테마/기대성 문구 감점")
    if any(word in text for word in PRICED_IN_WORDS):
        score -= 1
        reasons.append("가격 선반영 가능성 감점")
    if any(word in text for word in NOISE_WORDS):
        score -= 3
        reasons.append("홍보성 문구 감점")

    score = max(1, min(10, score))
    if not reasons:
        reasons.append("단순 정보성 뉴스")

    return ScoredNews(summary=summary, score=score, reasons=reasons, symbols=symbols, impact=impact)


def _score_asset_news(summaries: list[SummaryItem], asset_type: str, impact_cache: dict[str, WebImpact]) -> list[ScoredNews]:
    scored = [_score_news(summary, asset_type, impact_cache) for summary in summaries]
    important = [item for item in scored if item.score >= IMPORTANT_THRESHOLD]
    return sorted(important, key=lambda x: (x.score, x.summary.repeat_count), reverse=True)[:MAX_IMPORTANT_PER_ASSET]


def _split_by_asset(summaries: list[SummaryItem]) -> tuple[list[SummaryItem], list[SummaryItem]]:
    stock: list[SummaryItem] = []
    crypto: list[SummaryItem] = []
    for summary in summaries:
        if _category_asset(summary) == "crypto":
            crypto.append(summary)
        else:
            stock.append(summary)
    return stock, crypto


def _market_direction(items: list[ScoredNews], asset_label: str) -> list[str]:
    if not items:
        return [f"▫️ {asset_label}: 점수 {IMPORTANT_THRESHOLD}점 이상 중요 뉴스 없음"]

    sectors = Counter()
    reason_counter = Counter()
    for item in items:
        sectors.update(item.summary.sectors)
        reason_counter.update(item.reasons)

    top_sector = sectors.most_common(1)[0][0] if sectors else "섹터 불명확"
    top_reason = reason_counter.most_common(1)[0][0] if reason_counter else "정보성 뉴스"
    sector_text = ", ".join([f"{name} {count}건" for name, count in sectors.most_common(5)]) if sectors else "섹터 분류 부족"

    lines = [f"🔎 방향성: {top_sector} 중심으로 뉴스 강도 우세"]
    lines.append(f"🏷 주요 섹터: {sector_text}")
    lines.append(f"🧠 이유: {top_reason} 비중이 높아 시장 반응 후보로 분류")
    if any("매크로" in reason for item in items for reason in item.reasons):
        lines.append("🌐 매크로: 금리·환율·물가 변수는 개별 종목보다 시장 전체 변동성 요인으로 우선 반영")
    return lines


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
    if symbol.asset_type == "crypto":
        return "crypto"
    return "stock"


def _render_asset_section(lines: list[str], title: str, raw_count: int, items: list[ScoredNews]) -> None:
    lines.append(title)
    lines.append(SUB_DIVIDER)
    lines.append(f"📥 수집/중복제거 뉴스: {raw_count}건")
    lines.append(f"⭐ 중요 뉴스: {len(items)}건")
    lines.extend(_market_direction(items, title.replace("📈 ", "").replace("🪙 ", "")))
    lines.append("")

    lines.append("📌 핵심 뉴스")
    if items:
        for idx, item in enumerate(items, start=1):
            summary = item.summary
            symbol_text = ", ".join([f"{s.name}({s.ticker})" for s in item.symbols]) or "직접 종목 없음"
            reason_text = ", ".join(item.reasons[:3])
            lines.append(f"{_impact_icon(item.impact.impact_level)} {idx}) [{item.score}점] {summary.title}")
            lines.append(f"   ├ 관련: {symbol_text}")
            lines.append(f"   ├ 판단근거: {reason_text}")
            lines.append(f"   ├ 외부확인: {item.impact.impact_level} / 검색 {item.impact.result_count}건")
            if item.impact.latest_title:
                lines.append(f"   └ 확인뉴스: {item.impact.latest_title[:90]}")
            else:
                lines.append("   └ 확인뉴스: 외부 검색 부족")
    else:
        lines.append("▫️ 점수 기준을 통과한 뉴스 없음. 잡음으로 처리")
    lines.append("")

    lines.append("🎯 관련 종목·가격 전략")
    symbols = _unique_symbols(items)
    if symbols:
        for idx, (symbol, item) in enumerate(symbols, start=1):
            strategy = build_strategy(symbol.ticker, _asset_type_for_strategy(symbol), item.score, item.summary.risk)
            quote = strategy.quote
            quote_line = "가격확인불가"
            if quote and quote.price is not None:
                quote_line = f"현재가 {quote.price:,.2f} / 등락률 {quote.change_pct:+.2f}% / {quote.source} / {quote.timestamp}" if quote.change_pct is not None else f"현재가 {quote.price:,.2f} / {quote.source} / {quote.timestamp}"
            lines.append(f"{idx}) {symbol.name} / {symbol.ticker}")
            lines.append(f"   ├ 근거뉴스: {item.summary.title}")
            lines.append(f"   ├ 가격: {quote_line}")
            lines.append(f"   ├ 관점: {strategy.view}")
            lines.append(f"   ├ 진입: {strategy.entry}")
            lines.append(f"   ├ 손절: {strategy.stop}")
            lines.append(f"   ├ 목표: {strategy.target}")
            lines.append(f"   └ 리스크: {strategy.risk}")
    else:
        lines.append("▫️ 직접 언급 종목 없음. 섹터 흐름만 참고")
    lines.append("")


def build_markdown_report(
    summaries: list[SummaryItem],
    hours: int,
    timezone_name: str = "Asia/Seoul",
) -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    lines: list[str] = []
    impact_cache: dict[str, WebImpact] = {}

    stock_raw, crypto_raw = _split_by_asset(summaries)
    stock_items = _score_asset_news(stock_raw, "stock", impact_cache)
    crypto_items = _score_asset_news(crypto_raw, "crypto", impact_cache)
    total_important = len(stock_items) + len(crypto_items)

    lines.append("📰 텔레그램 뉴스 브리핑")
    lines.append(DIVIDER)
    lines.append(f"⏰ 기준: {now:%Y-%m-%d %H:%M} {timezone_name}")
    lines.append(f"🧭 범위: 최근 {hours}시간")
    lines.append(f"📌 중복 제거 후 분석 뉴스: {len(summaries)}건")
    lines.append(f"⭐ 중요 뉴스 선별: {total_important}건 / 기준 {IMPORTANT_THRESHOLD}점 이상")
    lines.append("")

    lines.append("🌐 전체 시장")
    lines.append(SUB_DIVIDER)
    overview = fetch_market_overview()
    if overview:
        for item in overview:
            lines.append(f"▫️ {item}")
    else:
        lines.append("▫️ 시장 데이터 확인 실패")
    lines.append("")

    _render_asset_section(lines, "📈 주식 뉴스", len(stock_raw), stock_items)
    _render_asset_section(lines, "🪙 코인/크립토 뉴스", len(crypto_raw), crypto_items)

    lines.append("🧩 공통 대응 기준")
    lines.append(SUB_DIVIDER)
    lines.append("✅ 볼 것: 실제 이벤트, 복수 채널 반복, 외부 뉴스 확인, 실시간 가격·거래대금 증가")
    lines.append("⛔ 제외: 단일 채널 홍보성 글, 관련주/수혜/전망만 있는 테마성 글, 이미 급등 후 나온 뉴스")
    lines.append("⚠️ 자동 전략은 뉴스·실시간 가격 기반 1차 필터입니다. 최종 진입 전 차트·호가·수급을 재확인하세요.")

    return "\n".join(lines)
