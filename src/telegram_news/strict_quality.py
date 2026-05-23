from __future__ import annotations

# 뉴스 제목 알림용 중요도 게이트.
# 기존 82/B+ 기준은 너무 엄격해서 실제로 볼 만한 뉴스까지 탈락했다.
# 제목만 보내는 모드에서는 '확정 고중요 뉴스'뿐 아니라 '관찰 가치 뉴스'도 통과시킨다.
CORE_TYPES = {"공시/확정", "이벤트", "실적", "리스크", "거시"}
WATCH_TYPES = {"가격반응", "테마", "정보"}
LOW_VALUE_TYPES = {"가격반응", "테마", "정보"}
MATERIALITY_THRESHOLD = 68
WATCH_THRESHOLD = 72
MAX_NEWS = 8


def materiality_score(cluster) -> int:
    best = cluster.best()
    text = f"{best.item.title} {best.item.body}".lower()
    score = cluster.score()

    if best.news_type in CORE_TYPES:
        score += 12
    elif best.news_type in WATCH_TYPES:
        score -= 8

    if best.symbols:
        score += 8
    elif best.news_type not in {"거시", "리스크"}:
        score -= 5

    if cluster.channel_count() >= 2:
        score += 6
    if len(cluster.items) >= 2:
        score += 4

    if best.impact.impact_level == "높음":
        score += 8
    elif best.impact.impact_level == "중간":
        score += 4
    elif best.impact.impact_level == "확인부족" and best.news_type not in {"공시/확정", "이벤트"}:
        score -= 4

    # 관련주/수혜/전망은 완전 제외하지 않고 감점만 한다. 제목 알림에서는 관찰 대상이 될 수 있다.
    if any(word in text for word in ["관련주", "수혜", "기대", "전망", "관심", "부각", "테마", "가능성"]):
        score -= 7

    # 급등/상한가 등은 사후성 뉴스라 감점하되, 시장 흐름 파악용으로 일부 통과 가능하게 한다.
    if any(word in text for word in ["급등", "상한가", "폭등", "신고가", "장대양봉", "강세", "상승세"]) and best.news_type != "공시/확정":
        score -= 8

    if not cluster.sectors() and best.news_type not in {"거시", "리스크"}:
        score -= 4

    return max(1, min(100, score))


def materiality_grade(cluster) -> str:
    score = materiality_score(cluster)
    if score >= 90:
        return "A"
    if score >= 80:
        return "B+"
    if score >= MATERIALITY_THRESHOLD:
        return "B"
    return "C"


def strict_filter(clusters):
    kept = []
    for cluster in clusters:
        best = cluster.best()
        score = materiality_score(cluster)

        if best.news_type in CORE_TYPES and score >= MATERIALITY_THRESHOLD:
            kept.append(cluster)
            continue

        # 테마/가격반응/정보성은 너무 많이 섞이면 잡음이 되므로 종목 직접 언급 또는 반복/외부확인이 있을 때만 통과.
        if best.news_type in WATCH_TYPES:
            has_support = bool(best.symbols) or cluster.channel_count() >= 2 or len(cluster.items) >= 2 or best.impact.impact_level in {"높음", "중간"}
            if has_support and score >= WATCH_THRESHOLD:
                kept.append(cluster)

    return sorted(kept, key=lambda c: (materiality_score(c), c.score()), reverse=True)[:MAX_NEWS]
