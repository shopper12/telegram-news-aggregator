from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
import math
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .noise_patterns import ADVISORY_WORDS, LOW_VALUE_WORDS, NOISE_WORDS, REPOST_WORDS


URL_RE = re.compile(r"https?://[^\s<>\]\[()]+", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
TITLE_CLEAN_RE = re.compile(r"[^0-9A-Za-z가-힣 ]+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:조|억|만|%|달러|원|억원|조원|bp|톤|건|명|배)?", re.IGNORECASE)
PROPER_RE = re.compile(r"(?:[A-Z]{2,10}|[가-힣A-Za-z0-9]+(?:전자|화학|증권|금융|중공업|바이오|에너지|테크|그룹|은행|공사|제약|반도체|정부|위원회))")
SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")

CLICKBAIT_WORDS = [
    "충격", "대박", "무조건", "지금 당장", "놓치면", "역대급", "이 종목", "급등주",
    "상한가 직행", "무료 공개", "단독 공개", "수익 인증", "추천드립니다",
]
VAGUE_WORDS = ["주목된다", "관심이 쏠린다", "귀추가 주목", "기대감이 커진다", "전망이다"]
CAUSE_WORDS = ["때문", "영향", "따라", "으로 인해", "배경", "증가", "감소", "확대", "축소", "상승", "하락", "개선", "악화"]
MARKET_WORDS = ["코스피", "코스닥", "나스닥", "s&p", "다우", "증시", "외국인", "기관", "순매수", "순매도"]
MACRO_WORDS = ["금리", "환율", "연준", "한은", "fomc", "cpi", "ppi", "고용", "국채", "달러", "유가", "관세"]
POLICY_WORDS = ["정부", "국회", "대통령", "장관", "정책", "규제", "법안", "위원회", "선거", "관세"]
MATERIAL_WORDS = ["공시", "수주", "계약", "실적", "매출", "영업이익", "승인", "허가", "소송", "제재", "배당", "증자"]

SOURCE_WEIGHTS = {
    "전자공시": 24.0,
    "거래소": 22.0,
    "금감원": 22.0,
    "연합뉴스": 17.0,
    "로이터": 17.0,
    "reuters": 17.0,
    "블룸버그": 17.0,
    "bloomberg": 17.0,
    "한국경제": 12.0,
    "매일경제": 12.0,
    "머니투데이": 10.0,
}
KEYWORD_WEIGHTS = {
    "공시": 15.0,
    "수주": 15.0,
    "계약": 14.0,
    "실적": 14.0,
    "승인": 12.0,
    "허가": 12.0,
    "소송": 10.0,
    "제재": 10.0,
    "금리": 9.0,
    "환율": 9.0,
    "fomc": 9.0,
    "cpi": 8.0,
    "관련주": -9.0,
    "수혜주": -9.0,
    "급등주": -12.0,
    "상한가": -8.0,
}
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref", "referrer", "source", "spm"}
MAX_CATEGORY_SHARE = 0.40


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _normalize_title(value: str) -> str:
    text = URL_RE.sub("", value)
    text = TITLE_CLEAN_RE.sub(" ", text.lower())
    return SPACE_RE.sub(" ", text).strip()


def _canonical_url(value: str) -> str:
    raw = _text(value).rstrip(".,;:!?")
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        host = parsed.netloc.lower().removeprefix("www.")
        path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
        query = []
        for key, val in parse_qsl(parsed.query, keep_blank_values=False):
            lower_key = key.lower()
            if lower_key.startswith("utm_") or lower_key in TRACKING_QUERY_KEYS:
                continue
            query.append((key, val))
        return urlunsplit((parsed.scheme.lower() or "https", host, path, urlencode(sorted(query)), ""))
    except Exception:
        return raw


def _article_urls(article: dict) -> set[str]:
    values: list[str] = []
    for key in ("url", "source_url", "message_url"):
        if article.get(key):
            values.append(_text(article.get(key)))
    for key in ("urls", "source_urls", "message_urls"):
        raw = article.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.extend(_text(item) for item in raw)
    values.extend(URL_RE.findall(" ".join(_text(article.get(key)) for key in ("title", "body", "text", "summary"))))
    return {url for url in (_canonical_url(value) for value in values) if url}


def _event_fingerprint(article: dict) -> set[str]:
    title = _normalize_title(_text(article.get("title") or article.get("headline")))
    tokens = {
        token for token in title.split()
        if len(token) >= 3 and token not in {"관련", "대한", "통해", "위해", "발표", "전망", "속보", "단독"}
    }
    numbers = set(re.findall(r"\d+(?:\.\d+)?", title))
    return set(list(tokens)[:10]) | numbers


def _article_body(article: dict) -> str:
    return " ".join(_text(article.get(key)) for key in ("title", "lead", "body", "text", "summary", "source"))


def _parse_datetime(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _age_minutes(article: dict) -> float | None:
    raw_age = article.get("age_minutes")
    try:
        if raw_age is not None:
            return max(0.0, float(raw_age))
    except Exception:
        pass

    values: list[Any] = []
    for key in ("published_at", "message_date", "date"):
        if article.get(key):
            values.append(article.get(key))
    for key in ("message_dates", "published_dates"):
        raw = article.get(key)
        if isinstance(raw, (list, tuple)):
            values.extend(raw)
    dates = [dt for dt in (_parse_datetime(value) for value in values) if dt]
    if not dates:
        return None
    latest = max(dates)
    return max(0.0, (datetime.now(timezone.utc) - latest).total_seconds() / 60.0)


def _broad_category(article: dict) -> str:
    text = _article_body(article).lower()
    if _contains_any(text, POLICY_WORDS):
        return "정치/정책"
    if _contains_any(text, MACRO_WORDS):
        return "거시"
    symbols = article.get("symbols") or article.get("tickers")
    if symbols:
        return "개별종목"
    if _contains_any(text, MARKET_WORDS):
        return "증시"
    category = article.get("category") or article.get("sector") or article.get("news_type") or "기타"
    if isinstance(category, (list, tuple, set)):
        return _text(next(iter(category), "기타"))
    return _text(category) or "기타"


def _is_low_quality(article: dict) -> bool:
    title = _text(article.get("title") or article.get("headline"))
    body = _article_body(article)
    compact = _normalize_title(title)
    market_impact = float(article.get("market_impact_score") or 0)

    if _contains_any(body, NOISE_WORDS + REPOST_WORDS):
        return True
    if _contains_any(body, ADVISORY_WORDS + CLICKBAIT_WORDS) and not _contains_any(body, MATERIAL_WORDS + MACRO_WORDS):
        return True
    if len(compact) < 12 and not NUMBER_RE.search(body) and not PROPER_RE.search(body) and market_impact < 68:
        return True
    if compact in {"속보", "단독", "긴급", "업데이트", "뉴스"}:
        return True
    if _contains_any(body, LOW_VALUE_WORDS) and market_impact < 88:
        return True
    return False


def score_article(article: dict) -> float:
    """Score one article by source, recency, impact, and keyword relevance."""
    text = _article_body(article).lower()
    score = 0.0

    for source, weight in SOURCE_WEIGHTS.items():
        if source.lower() in text:
            score += weight
            break

    age = _age_minutes(article)
    if age is None:
        score += 4.0
    elif age <= 30:
        score += 20.0
    elif age <= 90:
        score += 15.0
    elif age <= 240:
        score += 9.0
    elif age <= 720:
        score += 3.0
    else:
        score -= 6.0

    impact = float(article.get("market_impact_score") or article.get("importance_score") or 0)
    score += min(45.0, max(0.0, impact) * 0.45)

    for keyword, weight in KEYWORD_WEIGHTS.items():
        if keyword.lower() in text:
            score += weight
    if NUMBER_RE.search(text):
        score += 8.0
    if PROPER_RE.search(text):
        score += 5.0
    if _contains_any(text, CAUSE_WORDS):
        score += 4.0
    if len(_article_urls(article)) > 1:
        score += 2.0
    return score


def dedupe_articles(articles: list[dict], title_threshold: float = 0.85) -> list[dict]:
    """Keep one representative for the same URL, near-identical title, or event."""
    representatives: list[dict] = []
    for raw in articles:
        article = dict(raw)
        title = _normalize_title(_text(article.get("title") or article.get("headline")))
        if not title:
            continue
        urls = _article_urls(article)
        fingerprint = _event_fingerprint(article)
        matched_index: int | None = None

        for index, kept in enumerate(representatives):
            kept_title = _normalize_title(_text(kept.get("title") or kept.get("headline")))
            same_url = bool(urls and urls & _article_urls(kept))
            title_ratio = SequenceMatcher(None, title, kept_title).ratio()
            kept_fp = _event_fingerprint(kept)
            overlap = len(fingerprint & kept_fp)
            same_event = overlap >= 3 and title_ratio >= 0.70
            if same_url or title_ratio > title_threshold or same_event:
                matched_index = index
                break

        if matched_index is None:
            representatives.append(article)
            continue

        current = representatives[matched_index]
        if score_article(article) > score_article(current):
            merged = article
            merged["duplicate_count"] = int(current.get("duplicate_count") or 1) + int(article.get("duplicate_count") or 1)
            merged["duplicate_sources"] = sorted(set(
                [_text(current.get("source")), _text(article.get("source"))]
                + list(current.get("duplicate_sources") or [])
                + list(article.get("duplicate_sources") or [])
            ) - {""})
            representatives[matched_index] = merged
        else:
            current["duplicate_count"] = int(current.get("duplicate_count") or 1) + int(article.get("duplicate_count") or 1)
            current["duplicate_sources"] = sorted(set(
                [_text(current.get("source")), _text(article.get("source"))]
                + list(current.get("duplicate_sources") or [])
                + list(article.get("duplicate_sources") or [])
            ) - {""})

    return representatives


def select_top_articles(articles: list[dict], limit: int = 10) -> list[dict]:
    """Single article gate: quality filter -> dedupe -> score -> diversify."""
    clean = [dict(article) for article in articles if not _is_low_quality(article)]
    ranked = sorted(dedupe_articles(clean), key=score_article, reverse=True)
    category_cap = max(1, math.ceil(limit * MAX_CATEGORY_SHARE))
    counts: Counter[str] = Counter()
    selected: list[dict] = []

    for article in ranked:
        category = _broad_category(article)
        if counts[category] >= category_cap:
            continue
        article["selection_score"] = round(score_article(article), 2)
        article["selection_category"] = category
        selected.append(article)
        counts[category] += 1
        if len(selected) >= limit:
            break
    return selected


def _cluster_article(cluster: Any) -> dict:
    from .strict_quality import materiality_score

    best = cluster.best()
    item = best.item
    channels = list(getattr(item, "channels", []) or [])
    dates = list(getattr(item, "message_dates", []) or [])
    urls = list(getattr(item, "source_urls", []) or [])
    symbols = [getattr(symbol, "ticker", "") for symbol in cluster.symbols()]
    sectors = list(cluster.sectors())
    return {
        "title": _text(getattr(item, "title", "")),
        "body": _text(getattr(item, "body", "")),
        "source": ", ".join(channels),
        "source_urls": urls,
        "message_dates": dates,
        "category": sectors[0] if sectors else getattr(best, "news_type", "기타"),
        "news_type": getattr(best, "news_type", "기타"),
        "symbols": symbols,
        "market_impact_score": materiality_score(cluster),
        "_cluster": cluster,
    }


def select_top_clusters(clusters: list[Any]) -> list[Any]:
    """Actual strict-report selector used by the production pipeline."""
    from . import strict_quality as quality

    candidates: list[Any] = []
    for cluster in clusters:
        best = cluster.best()
        score = quality.materiality_score(cluster)
        low_value = quality._is_low_value_cluster(cluster)

        if low_value and not (
            score >= 88
            and best.news_type in quality.TRADE_CORE_TYPES
            and best.impact.impact_level == "높음"
        ):
            continue
        if best.news_type in quality.CORE_TYPES and score >= quality.MATERIALITY_THRESHOLD:
            candidates.append(cluster)
            continue
        if best.news_type in quality.WATCH_TYPES and quality._has_watch_support(cluster, score) and score >= quality.WATCH_THRESHOLD:
            candidates.append(cluster)

    selected = select_top_articles([_cluster_article(cluster) for cluster in candidates], limit=10)
    return [article["_cluster"] for article in selected]


def _split_sentences(text: str) -> list[str]:
    without_urls = URL_RE.sub("", text)
    compact = SPACE_RE.sub(" ", without_urls.replace("\r", "\n")).strip()
    if not compact:
        return []
    pieces = [piece.strip(" -•·") for piece in SENTENCE_RE.split(compact) if piece.strip(" -•·")]
    if len(pieces) <= 1:
        pieces = [piece.strip(" -•·") for piece in re.split(r"\s*[|/]\s*|\s{2,}", compact) if piece.strip(" -•·")]
    return pieces


def _summary_sentence_score(sentence: str, index: int) -> float:
    score = max(0.0, 5.0 - index * 0.25)
    if NUMBER_RE.search(sentence):
        score += 5.0
    if PROPER_RE.search(sentence):
        score += 4.0
    if _contains_any(sentence, MATERIAL_WORDS + MACRO_WORDS):
        score += 5.0
    if _contains_any(sentence, CAUSE_WORDS):
        score += 4.0
    if _contains_any(sentence, VAGUE_WORDS):
        score -= 5.0
    if len(sentence) < 18:
        score -= 4.0
    if len(sentence) > 190:
        score -= 2.0
    return score


def summarize_article(article: dict) -> str:
    """Extract a grounded 2-3 sentence summary without copying the whole lead."""
    title = _text(article.get("title"))
    body = _text(article.get("lead") or article.get("body") or article.get("text"))
    source = article.get("source") or article.get("channel") or article.get("channels") or "출처미상"
    published = article.get("published_at") or article.get("message_date") or article.get("date") or "시각미상"

    candidates = _split_sentences(body)
    if title and all(SequenceMatcher(None, _normalize_title(title), _normalize_title(sentence)).ratio() < 0.82 for sentence in candidates):
        candidates.insert(0, title)

    ranked = sorted(
        [(index, sentence, _summary_sentence_score(sentence, index)) for index, sentence in enumerate(candidates)],
        key=lambda row: row[2],
        reverse=True,
    )
    chosen: list[tuple[int, str]] = []
    for index, sentence, _score in ranked:
        normalized = _normalize_title(sentence)
        if not normalized:
            continue
        if any(SequenceMatcher(None, normalized, _normalize_title(existing)).ratio() > 0.86 for _, existing in chosen):
            continue
        chosen.append((index, sentence[:180].rstrip() + ("…" if len(sentence) > 180 else "")))
        if len(chosen) >= 3:
            break

    chosen.sort(key=lambda row: row[0])
    sentences = [sentence for _, sentence in chosen]
    if len(sentences) == 1 and title and SequenceMatcher(None, _normalize_title(title), _normalize_title(sentences[0])).ratio() < 0.82:
        sentences.insert(0, title[:150])
    if not sentences:
        sentences = [title[:150]] if title else ["원문에서 요약 가능한 사실 문장을 찾지 못했습니다."]

    summary = " ".join(sentences[:3]).strip()
    if isinstance(source, (list, tuple, set)):
        source = ", ".join(_text(value) for value in source if _text(value)) or "출처미상"
    return f"{summary}\n출처: {source} | 시각: {published}"


def dedupe_rows(rows: list, threshold: float = 0.85):
    """Deduplicate DB rows by canonical URL, title similarity, and event fingerprint."""
    from .normalizer import DedupedItem

    articles: list[dict] = []
    for row in rows:
        text = _text(row["text"])
        normalized = _text(row["normalized_text"])
        message_url = _text(row["message_url"]) if "message_url" in row.keys() else ""
        articles.append({
            "title": text.splitlines()[0][:180] if text else "",
            "body": text,
            "text": text,
            "normalized_text": normalized,
            "source": _text(row["channel_name"]),
            "category": _text(row["category"]),
            "message_date": _text(row["message_date"]),
            "message_url": message_url,
            "source_urls": [message_url] if message_url else [],
            "_row": row,
        })

    groups = dedupe_articles(articles, title_threshold=threshold)
    result: list[DedupedItem] = []
    for article in groups:
        row = article["_row"]
        duplicate_sources = list(article.get("duplicate_sources") or [])
        channel_names = sorted(set([_text(row["channel_name"])] + duplicate_sources) - {""})
        urls = sorted(_article_urls(article))
        result.append(DedupedItem(
            text=_text(article.get("text") or article.get("body")),
            channel_names=channel_names,
            categories=[_text(row["category"])],
            count=max(1, int(article.get("duplicate_count") or 1)),
            message_dates=[_text(row["message_date"])],
            message_urls=urls,
        ))
    return sorted(result, key=lambda item: item.message_dates[0] if item.message_dates else "", reverse=True)


def apply_unified_pipeline() -> None:
    """Patch all production entry points to the same selector and summarizer."""
    from . import app, normalizer, strict_quality, strict_report, summarizer

    if getattr(app, "_unified_news_pipeline_applied", False):
        return

    normalizer.deduplicate_rows = dedupe_rows
    app.deduplicate_rows = dedupe_rows
    summarizer.summarize = summarize_article

    strict_quality.score_article = score_article
    strict_quality.dedupe_articles = dedupe_articles
    strict_quality.select_top_articles = select_top_articles
    strict_quality.strict_filter = select_top_clusters
    strict_report.strict_filter = select_top_clusters

    app._unified_news_pipeline_applied = True
    print("[unified-pipeline] selector=dedupe+score+diversify summarizer=evidence-2to3-sentences")


def apply(api_module: Any | None = None) -> Any | None:
    apply_unified_pipeline()
    return api_module
