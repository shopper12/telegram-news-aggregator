from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter

from .summarizer import SummaryItem


KNOWN_KOREAN_NAMES = [
    "삼성전자",
    "SK하이닉스",
    "현대차",
    "기아",
    "한화에어로스페이스",
    "HD현대일렉트릭",
    "LS ELECTRIC",
    "두산에너빌리티",
    "우리기술",
    "서전기전",
    "파워넷",
    "비나텍",
    "성호전자",
]


BAD_TICKERS = {"AI", "SK", "KV", "ETF", "CEO", "SEC", "FED", "FOMC", "GDP", "CPI", "KOSPI", "KOSDAQ"}
DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
SUB_DIVIDER = "──────────────"


def _pick_names(summary: SummaryItem) -> list[str]:
    found: list[str] = []
    source = f"{summary.title} {summary.body} {' '.join(summary.keywords)}"

    for name in KNOWN_KOREAN_NAMES:
        if name.lower() in source.lower() and name not in found:
            found.append(name)

    for ticker in summary.tickers:
        if ticker not in BAD_TICKERS and ticker not in found:
            found.append(ticker)

    return found


def _top_names(summaries: list[SummaryItem], limit: int = 5) -> list[tuple[str, SummaryItem]]:
    result: list[tuple[str, SummaryItem]] = []
    seen: set[str] = set()

    for summary in sorted(summaries, key=lambda x: x.importance_score, reverse=True):
        for name in _pick_names(summary):
            if name in seen:
                continue
            seen.add(name)
            result.append((name, summary))
            if len(result) >= limit:
                return result

    return result


def _signal_icon(score: int) -> str:
    if score >= 9:
        return "🔥"
    if score >= 6:
        return "🟠"
    return "⚪"


def build_markdown_report(
    summaries: list[SummaryItem],
    hours: int,
    timezone_name: str = "Asia/Seoul",
) -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    lines: list[str] = []

    sector_counter = Counter()
    keyword_counter = Counter()
    for s in summaries:
        sector_counter.update(s.sectors)
        keyword_counter.update(s.keywords)

    top_sectors = sector_counter.most_common(5)
    top_summary = summaries[0] if summaries else None
    key_names = _top_names(summaries, limit=5)

    lines.append("📰 텔레그램 뉴스 브리핑")
    lines.append(DIVIDER)
    lines.append(f"⏰ 기준: {now:%Y-%m-%d %H:%M} {timezone_name}")
    lines.append(f"🧭 범위: 최근 {hours}시간")
    lines.append(f"📌 분석 뉴스: {len(summaries)}건")
    lines.append("")

    lines.append("📊 1. 시황")
    lines.append(SUB_DIVIDER)
    if not summaries:
        lines.append("▫️ 수집된 뉴스가 없습니다.")
    else:
        if top_sectors:
            sector_text = "  /  ".join([f"{name} {count}건" for name, count in top_sectors])
            lines.append(f"🔎 핵심 흐름: {sector_text}")
        else:
            lines.append("🔎 핵심 흐름: 뚜렷한 반복 섹터 없음")

        if top_summary:
            lines.append(f"⚡ 주요 이슈: {top_summary.title}")
            lines.append(f"🧠 시장 해석: {top_summary.judgment}")
    lines.append("")

    lines.append("📌 2. 주요 종목")
    lines.append(SUB_DIVIDER)
    if key_names:
        for idx, (name, summary) in enumerate(key_names, start=1):
            sectors = ", ".join(summary.sectors) if summary.sectors else "-"
            icon = _signal_icon(summary.importance_score)
            lines.append(f"{icon} {idx}) {name}")
            lines.append(f"   ├ 이슈: {summary.title}")
            lines.append(f"   ├ 섹터: {sectors}")
            lines.append(f"   └ 뉴스 강도: {summary.importance_score}점 / 반복 {summary.repeat_count}회")
    else:
        lines.append("▫️ 명확한 종목명 부족")
        lines.append("▫️ 오늘은 섹터 흐름 중심으로만 해석")
    lines.append("")

    lines.append("🧩 3. 대응 전략")
    lines.append(SUB_DIVIDER)
    if key_names:
        for idx, (name, summary) in enumerate(key_names, start=1):
            lines.append(f"▶️ {idx}) {name}")
            lines.append(f"   ✅ 볼 것: {summary.trade_view}")
            lines.append(f"   ⚠️ 주의: {summary.risk}")
    elif top_summary:
        lines.append(f"✅ 볼 것: {top_summary.trade_view}")
        lines.append(f"⚠️ 주의: {top_summary.risk}")
    else:
        lines.append("▫️ 신규 대응 없음. 뉴스 수집량 부족")

    lines.append("")
    lines.append(DIVIDER)
    lines.append("⚠️ 뉴스 기반 브리핑입니다. 매수·매도 확정 신호가 아니며, 실제 진입 전 가격·거래대금·수급 확인 필요.")

    return "\n".join(lines)
