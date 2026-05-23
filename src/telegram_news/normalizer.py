from __future__ import annotations

import re
from dataclasses import dataclass
from rapidfuzz import fuzz


URL_RE = re.compile(r"https?://\S+")
SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    text = URL_RE.sub("", text)
    text = text.replace("\u200b", "")
    text = SPACE_RE.sub(" ", text)
    return text.strip().lower()


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


def deduplicate_rows(rows: list, threshold: int = 96) -> list[DedupedItem]:
    """거의 같은 뉴스만 병합한다.

    중복 병합 후에도 원문 Telegram 메시지 URL은 보존한다.
    """
    groups: list[dict] = []

    for row in rows:
        norm = row["normalized_text"]
        if not norm:
            continue

        matched = None
        for group in groups:
            if fuzz.token_set_ratio(norm, group["normalized"]) >= threshold:
                matched = group
                break

        url = _row_message_url(row)
        if matched is None:
            groups.append(
                {
                    "normalized": norm,
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
