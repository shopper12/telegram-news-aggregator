from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

URL_RE = re.compile(r"https?://\S+")
SPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^0-9a-z가-힣 ]", re.IGNORECASE)


def normalize_text(text: str) -> str:
    text = URL_RE.sub("", text)
    text = text.replace("\u200b", "")
    text = SPACE_RE.sub(" ", text)
    return text.strip().lower()


def _title_key(text: str) -> str:
    head = " ".join(str(text or "").replace("\n", " ").split())[:160]
    head = URL_RE.sub("", head).lower()
    head = PUNCT_RE.sub(" ", head)
    return SPACE_RE.sub(" ", head).strip()


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class DedupedItem:
    text: str
    channel_names: list[str]
    categories: list[str]
    count: int
    message_dates: list[str]
    message_urls: list[str]


def _row_message_url(row) -> str | None:
    try:
        value = row["message_url"]
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def deduplicate_rows(rows: list, threshold: float = 0.85) -> list[DedupedItem]:
    """Merge near-duplicate Telegram messages by title-level similarity.

    SequenceMatcher ratio > 0.85 catches repeated headlines from different
    Telegram channels while preserving source URLs and timestamps.
    """
    groups: list[dict] = []

    for row in rows:
        norm = row["normalized_text"]
        if not norm:
            continue

        key = _title_key(norm)
        matched = None
        for group in groups:
            if _similarity(key, group["title_key"]) > threshold:
                matched = group
                break

        url = _row_message_url(row)
        if matched is None:
            groups.append(
                {
                    "normalized": norm,
                    "title_key": key,
                    "text": row["text"],
                    "channel_names": {row["channel_name"]},
                    "categories": {row["category"]},
                    "count": 1,
                    "message_dates": [row["message_date"]],
                    "message_urls": [url] if url else [],
                }
            )
        else:
            matched["channel_names"].add(row["channel_name"])
            matched["categories"].add(row["category"])
            matched["count"] += 1
            matched["message_dates"].append(row["message_date"])
            if url and url not in matched["message_urls"]:
                matched["message_urls"].append(url)

    result: list[DedupedItem] = []
    for g in groups:
        result.append(
            DedupedItem(
                text=g["text"],
                channel_names=sorted(g["channel_names"]),
                categories=sorted(g["categories"]),
                count=g["count"],
                message_dates=sorted(g["message_dates"], reverse=True),
                message_urls=g["message_urls"],
            )
        )

    return sorted(result, key=lambda x: x.message_dates[0], reverse=True)
