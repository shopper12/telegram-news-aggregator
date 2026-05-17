from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter

from .summarizer import SummaryItem
from .symbol_resolver import resolve_symbols, ResolvedSymbol
from .web_research import judge_web_impact


DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
SUB_DIVIDER = "──────────────"


def _is_actionable(summary: SummaryItem) -> bool:
    text = f"{summary.title} {summary.body}".lower()
    event_words = ["단독", "속보", "수주", "계약", "공급", "납품", "승인", "허가", "상장", "인수", "합병", "실적", "가이던스"]
    risk_words = ["급락", "제재", "조사", "소송", "유상증자", "상장폐지", "거래정지"]
    symbols = resolve_symbols(f"{summary.title} {summary.body}", summary.categories, summary.tickers)
    return (
        summary.repeat_count >= 2
        or summary.importance_score >= 7
        or any(word in text for word in event_words)
        or any(word in text for word in risk_words)
        or bool(symbols)
    )


def _important_news(summaries: list[SummaryItem], limit: int = 8) -> list[SummaryItem]:
    filtered = [s for s in summaries if _is_actionable(s)]
    if not filtered:
        filtered = summaries
    return sorted(filtered, key=lambda x: (x.importance_score, x.repeat_count), reverse=True)[:limit]


def _signal_icon(score: int) -> str:
    if score >= 9:
        return "🔥"
    if score >= 6:
        return "🟠"
    return "⚪"


def _impact_icon(level: str) -> str:
    if level == "높음":
        return "🔥"
    if level == "중간":
        return "🟠"
    if level == "낮음":
        return "⚪"
    return "▫️"


def _asset_bucket(summary: SummaryItem) -> str:
    cats = set(summary.categories)
    sectors = set(summary.sectors)
    text = f"{summary.title} {summary.body}".lower()
    if "crypto" in cats or any(s in sectors for s in ["bitcoin", "ethereum", "solana", "xrp", "sui", "defi", "ai_coin", "rwa"]):
        return "crypto"
    if any(word in text for word in ["btc", "비트코인", "코인", "업비트", "바이낸스", "온체인"]):
        return "crypto"
    return "stock"


def _split_by_asset(summaries: list[SummaryItem]) -> tuple[list[SummaryItem], list[SummaryItem]]:
    stock: list[SummaryItem] = []
    crypto: list[SummaryItem] = []
    for summary in summaries:
        if _asset_bucket(summary) == "crypto":
            crypto.append(summary)
        else:
            stock.append(summary)
    return stock, crypto


def _unique_symbols(summaries: list[SummaryItem], asset_type: str, limit: int = 5) -> list[tuple[ResolvedSymbol, SummaryItem]]:
    result: list[tuple[ResolvedSymbol, SummaryItem]] = []
    seen: set[str] = set()
    for summary in summaries:
        symbols = resolve_symbols(f"{summary.title} {summary.body}", summary.categories, summary.tickers)
        for symbol in symbols:
            if asset_type == "crypto" and symbol.asset_type != "crypto":
                continue
            if asset_type == "stock" and symbol.asset_type == "crypto":
                continue
            if symbol.ticker in seen:
                continue
            seen.add(symbol.ticker)
            result.append((symbol, summary))
            if len(result) >= limit:
                return result
    return result


def _render_asset_section(lines: list[str], title: str, summaries: list[SummaryItem], asset_type: str) -> None:
    lines.append(title)
    lines.append(SUB_DIVIDER)

    if not summaries:
        lines.append("▫️ 중요 뉴스 없음")
        lines.append("")
        return

    sector_counter = Counter()
    for summary in summaries:
        sector_counter.update(summary.sectors)
    if sector_counter:
        sectors = "  /  ".join([f"{name} {count}건" for name, count in sector_counter.most_common(4)])
        lines.append(f"🔎 핵심 흐름: {sectors}")

    lines.append("⭐ 중요 뉴스")
    for idx, summary in enumerate(summaries[:5], start=1):
        icon = _signal_icon(summary.importance_score)
        symbols = resolve_symbols(f"{summary.title} {summary.body}", summary.categories, summary.tickers)
        if asset_type == "crypto":
            symbols = [s for s in symbols if s.asset_type == "crypto"]
        else:
            symbols = [s for s in symbols if s.asset_type != "crypto"]
        symbol_text = ", ".join([f"{s.name}({s.ticker})" for s in symbols]) or "종목명 미확정"
        impact_query = symbol_text if symbol_text != "종목명 미확정" else summary.title[:60]
        impact = judge_web_impact(impact_query)
        lines.append(f"{icon} {idx}) {summary.title}")
        lines.append(f"   ├ 관련: {symbol_text}")
        lines.append(f"   ├ 실시간 영향도: {_impact_icon(impact.impact_level)} {impact.impact_level} / 검색 {impact.result_count}건")
        if impact.latest_title:
            lines.append(f"   ├ 외부확인: {impact.latest_title[:90]}")
        lines.append(f"   └ 판단: {summary.trade_view}")

    symbols = _unique_symbols(summaries, asset_type=asset_type, limit=5)
    lines.append("📌 주요 종목")
    if symbols:
        for idx, (symbol, summary) in enumerate(symbols, start=1):
            impact = judge_web_impact(f"{symbol.name} {symbol.ticker} 뉴스")
            lines.append(f"{_impact_icon(impact.impact_level)} {idx}) {symbol.name} / {symbol.ticker}")
            lines.append(f"   ├ 이슈: {summary.title}")
            lines.append(f"   ├ 영향도: {impact.impact_level}")
            lines.append(f"   └ 대응: {summary.trade_view}")
    else:
        lines.append("▫️ 명확한 주요 종목 부족")
    lines.append("")


def build_markdown_report(
    summaries: list[SummaryItem],
    hours: int,
    timezone_name: str = "Asia/Seoul",
) -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    lines: list[str] = []

    important = _important_news(summaries, limit=10)
    stock_news, crypto_news = _split_by_asset(important)

    lines.append("📰 텔레그램 뉴스 브리핑")
    lines.append(DIVIDER)
    lines.append(f"⏰ 기준: {now:%Y-%m-%d %H:%M} {timezone_name}")
    lines.append(f"🧭 범위: 최근 {hours}시간")
    lines.append(f"📌 중복 제거 후 분석 뉴스: {len(summaries)}건")
    lines.append(f"⭐ 중요 뉴스 선별: {len(important)}건")
    lines.append("")

    _render_asset_section(lines, "📈 1. 주식 뉴스", stock_news, "stock")
    _render_asset_section(lines, "🪙 2. 코인/크립토 뉴스", crypto_news, "crypto")

    lines.append("🧩 3. 공통 대응 기준")
    lines.append(SUB_DIVIDER)
    lines.append("✅ 볼 것: 복수 채널 반복, 외부 뉴스 검색 결과, 실제 거래대금 증가, 장중 고점 돌파")
    lines.append("⛔ 피할 것: 단일 채널 홍보성 뉴스, 이미 장대양봉 후 나온 재탕 뉴스, 종목명 불명확한 테마성 글")
    lines.append("")
    lines.append(DIVIDER)
    lines.append("⚠️ 뉴스 기반 브리핑입니다. 매수·매도 확정 신호가 아니며, 실제 진입 전 가격·거래대금·수급 확인 필요.")

    return "\n".join(lines)
