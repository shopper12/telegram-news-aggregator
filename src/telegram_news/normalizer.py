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


def deduplicate_rows(rows: list, threshold: int = 96) -> list[DedupedItem]:
    """거의 같은 뉴스만 병합한다.

    기존 threshold=88은 제목 일부가 비슷한 뉴스까지 한 덩어리로 묶을 수 있었다.
    이제 중복은 '동일/거의 동일 원문 제거' 용도만 수행하고,
    중요도 판단은 반복 횟수가 아니라 뉴스 자체 내용에서 한다.
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

        if matched is None:
            groups.append(
                {
                    "normalized": norm,
                    "text": row["text"],
                    "channel_names": {row["channel_name"]},
                    "categories": {row["category"]},
                    "count": 1,
                    "message_dates": [row["message_date"]],
                }
            )
        else:
            matched["channel_names"].add(row["channel_name"])
            matched["categories"].add(row["category"])
            matched["count"] += 1
            matched["message_dates"].append(row["message_date"])

    result: list[DedupedItem] = []
    for g in groups:
        result.append(
            DedupedItem(
                text=g["text"],
                channel_names=sorted(g["channel_names"]),
                categories=sorted(g["categories"]),
                count=g["count"],
                message_dates=sorted(g["message_dates"], reverse=True),
            )
        )

    # 반복 출현이 아니라 최신성 기준으로 정렬한다.
    return sorted(result, key=lambda x: x.message_dates[0], reverse=True)
