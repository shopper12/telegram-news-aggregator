from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests


HIGH_IMPACT_WORDS = [
    "단독", "속보", "수주", "계약", "공급", "승인", "허가", "상장", "인수", "합병",
    "실적", "어닝", "가이던스", "제재", "조사", "소송", "거래정지", "상장폐지",
    "ETF", "SEC", "Fed", "FOMC", "금리", "환율", "유가", "전쟁", "관세",
]


@dataclass(frozen=True)
class WebImpact:
    query: str
    result_count: int
    high_impact_hits: int
    latest_title: str | None
    impact_level: str


def _parse_pub_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def search_google_news(query: str, limit: int = 5) -> list[dict[str, str]]:
    if not query.strip():
        return []

    url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=ko&gl=KR&ceid=KR:ko"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "telegram-news-aggregator/0.1"},
            timeout=8,
        )
        resp.raise_for_status()
    except Exception:
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return []

    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "link": link, "pubDate": pub_date})
    return items


def judge_web_impact(query: str, limit: int = 5) -> WebImpact:
    results = search_google_news(query, limit=limit)
    titles = [item["title"] for item in results]
    joined = " ".join(titles)
    high_hits = sum(1 for word in HIGH_IMPACT_WORDS if word.lower() in joined.lower())

    if len(results) >= 4 or high_hits >= 2:
        level = "높음"
    elif len(results) >= 2 or high_hits >= 1:
        level = "중간"
    elif len(results) == 1:
        level = "낮음"
    else:
        level = "확인부족"

    return WebImpact(
        query=query,
        result_count=len(results),
        high_impact_hits=high_hits,
        latest_title=titles[0] if titles else None,
        impact_level=level,
    )
