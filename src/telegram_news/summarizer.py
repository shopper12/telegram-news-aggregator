from __future__ import annotations

from dataclasses import dataclass
from .normalizer import DedupedItem
from .extractor import extract_signals


@dataclass(frozen=True)
class SummaryItem:
    title: str
    body: str
    channels: list[str]
    categories: list[str]
    repeat_count: int
    sectors: list[str]
    keywords: list[str]
    tickers: list[str]
    importance_score: int


def _make_title(text: str, max_len: int = 80) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= max_len else one_line[: max_len - 1] + "…"


def local_summarize(items: list[DedupedItem], limit: int = 15) -> list[SummaryItem]:
    summaries: list[SummaryItem] = []

    for item in items[:limit]:
        sig = extract_signals(item.text, repeat_count=item.count)
        summaries.append(
            SummaryItem(
                title=_make_title(item.text),
                body=item.text,
                channels=item.channel_names,
                categories=item.categories,
                repeat_count=item.count,
                sectors=sig.sectors,
                keywords=sig.keywords,
                tickers=sig.tickers,
                importance_score=sig.importance_score,
            )
        )

    return sorted(summaries, key=lambda x: x.importance_score, reverse=True)


def openai_summarize_if_available(
    items: list[DedupedItem],
    api_key: str | None,
    model: str,
    limit: int = 15,
) -> list[SummaryItem]:
    # 1차 구현은 로컬 규칙 기반 요약을 기본값으로 둡니다.
    # OpenAI API 기반 재요약은 다음 단계에서 프롬프트/비용 통제를 붙여 확장합니다.
    return local_summarize(items, limit=limit)
