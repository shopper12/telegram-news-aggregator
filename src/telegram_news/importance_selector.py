from __future__ import annotations

from typing import Any

from . import unified_pipeline as base

# Preserve the URL/title/event deduplicator before install() replaces public hooks.
_BASE_DEDUPE_ARTICLES = base.dedupe_articles

STOCK_CONTEXT_WORDS = [
    "종목", "주가", "증시", "코스피", "코스닥", "나스닥", "상장", "공시",
    "수주", "계약", "실적", "매출", "영업이익", "배당", "자사주", "증자",
    "승인", "허가", "소송", "제재", "기관", "외국인", "순매수", "순매도",
]
MARKET_CONTEXT_WORDS = [
    "금리", "환율", "연준", "한은", "fomc", "cpi", "ppi", "국채", "달러",
    "유가", "관세", "정책", "규제", "수출", "반도체", "ai", "데이터센터",
]
EVENT_WORDS = [
    "공시", "수주", "계약", "실적", "매출", "영업이익", "승인", "허가",
    "인수", "합병", "상장", "배당", "자사주", "증자", "소송", "제재",
]


def _as_count(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    if value in (None, ""):
        return 0
    return 1


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def score_article(article: dict) -> float:
    """Score only content importance, Telegram repetition, recency, and stock relevance."""
    text = base._article_body(article).lower()
    score = 0.0

    age = base._age_minutes(article)
    if age is None:
        score += 5.0
    elif age <= 30:
        score += 22.0
    elif age <= 90:
        score += 17.0
    elif age <= 240:
        score += 11.0
    elif age <= 720:
        score += 5.0

    impact = float(article.get("market_impact_score") or article.get("importance_score") or 0)
    score += min(50.0, max(0.0, impact) * 0.50)

    symbols = article.get("symbols") or article.get("tickers") or []
    symbol_count = _as_count(symbols)
    if symbol_count:
        score += 28.0 + min(12.0, symbol_count * 4.0)

    if base._contains_any(text, EVENT_WORDS):
        score += 16.0
    if base._contains_any(text, STOCK_CONTEXT_WORDS):
        score += 10.0
    if base._contains_any(text, MARKET_CONTEXT_WORDS):
        score += 7.0
    if base.NUMBER_RE.search(text):
        score += 8.0
    if base.PROPER_RE.search(text):
        score += 5.0
    if base._contains_any(text, base.CAUSE_WORDS):
        score += 4.0

    repeat_count = max(
        1,
        _safe_int(article.get("repeat_count"), 0),
        _safe_int(article.get("duplicate_count"), 0),
    )
    channel_count = max(
        _as_count(article.get("channels")),
        _as_count(article.get("duplicate_sources")),
        _safe_int(article.get("channel_count"), 0),
    )
    score += min(14.0, max(0, repeat_count - 1) * 2.0)
    score += min(10.0, max(0, channel_count - 1) * 2.0)

    return score


def dedupe_articles(articles: list[dict], title_threshold: float = 0.85) -> list[dict]:
    """Merge the same event while preserving repetition as an importance signal."""
    return _BASE_DEDUPE_ARTICLES(articles, title_threshold=title_threshold)


def select_top_articles(articles: list[dict], limit: int = 10) -> list[dict]:
    """Dedupe and rank without advertising, clickbait, or low-value exclusion rules."""
    usable = []
    for raw in articles:
        article = dict(raw)
        title = base._text(article.get("title") or article.get("headline"))
        body = base._article_body(article)
        if not title and not body:
            continue
        usable.append(article)

    ranked = sorted(dedupe_articles(usable), key=score_article, reverse=True)
    selected: list[dict] = []
    for article in ranked[:limit]:
        article["selection_score"] = round(score_article(article), 2)
        article["selection_category"] = base._broad_category(article)
        article["selection_basis"] = "importance_and_stock_relevance"
        selected.append(article)
    return selected


def _cluster_article(cluster: Any) -> dict:
    article = base._cluster_article(cluster)
    try:
        article["repeat_count"] = max(1, len(getattr(cluster, "items", []) or []))
    except Exception:
        article["repeat_count"] = 1
    try:
        article["channel_count"] = int(cluster.channel_count())
    except Exception:
        article["channel_count"] = _as_count(article.get("source"))
    return article


def select_top_clusters(clusters: list[Any]) -> list[Any]:
    """Production selector: every non-empty Telegram cluster competes by relevance score."""
    articles: list[dict] = []
    for cluster in clusters:
        try:
            article = _cluster_article(cluster)
        except Exception:
            continue
        if not base._text(article.get("title")) and not base._text(article.get("body")):
            continue
        articles.append(article)

    selected = select_top_articles(articles, limit=10)
    return [article["_cluster"] for article in selected]


def install() -> None:
    from . import strict_quality, strict_report

    base.score_article = score_article
    base.dedupe_articles = dedupe_articles
    base.select_top_articles = select_top_articles
    base.select_top_clusters = select_top_clusters

    strict_quality.score_article = score_article
    strict_quality.dedupe_articles = dedupe_articles
    strict_quality.select_top_articles = select_top_articles
    strict_quality.strict_filter = select_top_clusters
    strict_report.strict_filter = select_top_clusters

    print("[importance-selector] dedupe then rank by importance, repetition, recency, and stock relevance; no ad/clickbait exclusion")
