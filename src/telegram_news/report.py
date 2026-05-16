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

    lines.append("# 텔레그램 뉴스 판단 요약")
    lines.append("")
    lines.append(f"- 기준시각: {now:%Y-%m-%d %H:%M:%S} {timezone_name}")
    lines.append(f"- 수집범위: 최근 {hours}시간")
    lines.append(f"- 분석 뉴스 수: {len(summaries)}건")
    lines.append("")

    sector_counter = Counter()
    keyword_counter = Counter()
    ticker_counter = Counter()

    for s in summaries:
        sector_counter.update(s.sectors)
        keyword_counter.update(s.keywords)
        ticker_counter.update(s.tickers)

    lines.append("## 1. 지금 반복되는 섹터/키워드")
    lines.append("")
    if sector_counter:
        for k, v in sector_counter.most_common(10):
            lines.append(f"- {k}: {v}건")
    else:
        lines.append("- 감지된 섹터 없음")
    lines.append("")

    lines.append("## 2. 언급 티커")
    lines.append("")
    if ticker_counter:
        for k, v in ticker_counter.most_common(15):
            lines.append(f"- {k}: {v}회")
    else:
        lines.append("- 감지된 영문 티커 없음")
    lines.append("")

    lines.append("## 3. 우선순위 뉴스 판단")
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
        lines.append(f"- 판단: {s.judgment}")
        lines.append(f"- 매매 관점: {s.trade_view}")
        lines.append(f"- 리스크: {s.risk}")
        lines.append("")

    lines.append("## 4. 결론")
    lines.append("")
    lines.append("- 이 요약은 텔레그램 원문 복붙이 아니라 반복도·키워드·섹터·이벤트성을 기준으로 재분류한 판단형 요약이다.")
    lines.append("- 단, 현재 버전은 뉴스 기반 필터다. 최종 매수/매도는 실시간 가격·거래대금·수급 확인 후 판단해야 한다.")
    lines.append("- 급등·상한가·신고가 문구가 있는 뉴스는 신규 추격보다 눌림/재돌파 확인으로 처리한다.")

    return "\n".join(lines)
