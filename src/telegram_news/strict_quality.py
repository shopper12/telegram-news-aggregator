from __future__ import annotations

MATERIAL_TYPES = {"공시/확정", "이벤트", "실적", "리스크", "거시"}
LOW_VALUE_TYPES = {"가격반응", "테마", "정보"}
MATERIALITY_THRESHOLD = 82


def materiality_score(cluster) -> int:
    best = cluster.best()
    text = f"{best.item.title} {best.item.body}".lower()
    score = cluster.score()
    if best.news_type in MATERIAL_TYPES:
        score += 12
    if best.news_type in LOW_VALUE_TYPES:
        score -= 35
    if best.symbols:
        score += 6
    if cluster.channel_count() >= 2:
        score += 5
    if len(cluster.items) >= 2:
        score += 3
    if best.impact.impact_level == "높음":
        score += 8
    elif best.impact.impact_level == "중간":
        score += 4
    elif best.impact.impact_level == "확인부족" and best.news_type not in {"공시/확정", "이벤트"}:
        score -= 8
    if any(word in text for word in ["관련주", "수혜", "기대", "전망", "관심", "부각", "테마", "가능성"]):
        score -= 14
    if any(word in text for word in ["급등", "상한가", "폭등", "신고가", "장대양봉", "강세", "상승세"]) and best.news_type != "공시/확정":
        score -= 16
    if not cluster.sectors() and best.news_type not in {"거시", "리스크"}:
        score -= 8
    return max(1, min(100, score))


def materiality_grade(cluster) -> str:
    score = materiality_score(cluster)
    if score >= 92:
        return "A"
    if score >= MATERIALITY_THRESHOLD:
        return "B+"
    if score >= 72:
        return "B"
    return "C"


def strict_filter(clusters):
    kept = [
        c for c in clusters
        if c.best().news_type in MATERIAL_TYPES and materiality_score(c) >= MATERIALITY_THRESHOLD
    ]
    return sorted(kept, key=lambda c: (materiality_score(c), c.score()), reverse=True)[:3]
