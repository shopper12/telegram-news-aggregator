from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter

from .summarizer import SummaryItem


def build_markdown_report(
    summaries: list[SummaryItem],
    hours: int,
    timezone_name: str = "Asia/Seoul",
) -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    lines: list[str] = []

    lines.append("# 텔레그램 뉴스 종합")
    lines.append("")
    lines.append(f"- 기준시각: {now:%Y-%m-%d %H:%M:%S} {timezone_name}")
    lines.append(f"- 수집범위: 최근 {hours}시간")
    lines.append(f"- 주요 뉴스 수: {len(summaries)}건")
    lines.append("")

    sector_counter = Counter()
    keyword_counter = Counter()
    ticker_counter = Counter()

    for s in summaries:
        sector_counter.update(s.sectors)
        keyword_counter.update(s.keywords)
        ticker_counter.update(s.tickers)

    lines.append("## 1. 반복 섹터/키워드")
    lines.append("")
    if sector_counter:
        for k, v in sector_counter.most_common(10):
            lines.append(f"- {k}: {v}건")
    else:
        lines.append("- 감지된 섹터 없음")
    lines.append("")

    lines.append("## 2. 티커 후보")
    lines.append("")
    if ticker_counter:
        for k, v in ticker_counter.most_common(15):
            lines.append(f"- {k}: {v}회")
    else:
        lines.append("- 감지된 영문 티커 없음")
    lines.append("")

    lines.append("## 3. 중요 뉴스")
    lines.append("")
    for idx, s in enumerate(summaries, start=1):
        sectors = ", ".join(s.sectors) if s.sectors else "-"
        tickers = ", ".join(s.tickers) if s.tickers else "-"
        channels = ", ".join(s.channels)
        keywords = ", ".join(s.keywords[:8]) if s.keywords else "-"

        lines.append(f"### {idx}. {s.title}")
        lines.append("")
        lines.append(f"- 중요도: {s.importance_score}")
        lines.append(f"- 반복출현: {s.repeat_count}회")
        lines.append(f"- 채널: {channels}")
        lines.append(f"- 섹터: {sectors}")
        lines.append(f"- 티커: {tickers}")
        lines.append(f"- 키워드: {keywords}")
        lines.append("")
        lines.append(s.body[:700])
        lines.append("")

    lines.append("## 4. 매매 관점")
    lines.append("")
    lines.append("- 복수 채널 반복 + 거래대금 동반 이슈만 우선 후보로 봅니다.")
    lines.append("- 뉴스만 있고 가격·거래량 확인이 안 되는 종목은 제외합니다.")
    lines.append("- 이미 장대양봉이 나온 종목은 추격보다 눌림/재돌파 확인이 우선입니다.")
    lines.append("- 이 리포트는 뉴스 필터이며, 최종 진입가는 실시간 가격·거래량·수급 검증 후 산정해야 합니다.")

    return "\n".join(lines)
