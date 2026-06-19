from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebImpact:
    query: str
    result_count: int
    high_impact_hits: int
    latest_title: str | None
    impact_level: str


def search_google_news(query: str, limit: int = 5) -> list[dict[str, str]]:
    return []


def judge_web_impact(query: str, limit: int = 5) -> WebImpact:
    return WebImpact(
        query=query,
        result_count=0,
        high_impact_hits=0,
        latest_title=None,
        impact_level="telegram_source_only",
    )
