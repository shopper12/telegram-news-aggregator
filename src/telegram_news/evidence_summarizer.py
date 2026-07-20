from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any


URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
TITLE_RE = re.compile(r"[^0-9A-Za-z가-힣 ]+")
SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:조|억|만|%|달러|원|억원|조원|bp|톤|건|명|배)?", re.IGNORECASE)
PROPER_RE = re.compile(r"(?:[A-Z]{2,10}|[가-힣A-Za-z0-9]+(?:전자|화학|증권|금융|중공업|바이오|에너지|테크|그룹|은행|공사|제약|반도체|정부|위원회|시스템))")
FACT_WORDS = ["공시", "수주", "계약", "실적", "매출", "영업이익", "승인", "허가", "금리", "환율", "연준", "한은", "fda", "소송", "제재"]
CAUSE_WORDS = ["때문", "영향", "따라", "으로 인해", "배경", "원인", "증가", "감소", "확대", "축소", "개선", "악화", "정책"]
VAGUE_WORDS = ["주목된다", "관심이 쏠린다", "귀추가 주목", "기대감이 커진다", "전망이다"]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _normalize(text: str) -> str:
    value = URL_RE.sub("", text)
    value = TITLE_RE.sub(" ", value.lower())
    return SPACE_RE.sub(" ", value).strip()


def _split(text: str) -> list[str]:
    value = URL_RE.sub("", text.replace("\r", "\n"))
    pieces = [piece.strip(" -•·") for piece in SENTENCE_RE.split(value) if piece.strip(" -•·")]
    if len(pieces) <= 1:
        pieces = [piece.strip(" -•·") for piece in re.split(r"\s*[|/]\s*|\s{2,}", value) if piece.strip(" -•·")]
    return pieces


def _score(sentence: str, index: int) -> float:
    score = max(0.0, 5.0 - index * 0.25)
    if NUMBER_RE.search(sentence):
        score += 5.0
    if PROPER_RE.search(sentence):
        score += 4.0
    if _contains_any(sentence, FACT_WORDS):
        score += 5.0
    if _contains_any(sentence, CAUSE_WORDS):
        score += 4.0
    if _contains_any(sentence, VAGUE_WORDS):
        score -= 7.0
    if len(sentence) < 18:
        score -= 4.0
    if len(sentence) > 190:
        score -= 2.0
    return score


def _unique(chosen: list[tuple[int, str, float]], sentence: str) -> bool:
    normalized = _normalize(sentence)
    return bool(normalized) and not any(
        SequenceMatcher(None, normalized, _normalize(existing)).ratio() > 0.86
        for _, existing, _ in chosen
    )


def summarize_article(article: dict) -> str:
    """Return a grounded 2-3 sentence summary with facts and causal context."""
    title = _text(article.get("title"))
    body = _text(article.get("lead") or article.get("body") or article.get("text"))
    source = article.get("source") or article.get("channel") or article.get("channels") or "출처미상"
    published = article.get("published_at") or article.get("message_date") or article.get("date") or "시각미상"

    candidates = _split(body)
    if title and all(SequenceMatcher(None, _normalize(title), _normalize(sentence)).ratio() < 0.82 for sentence in candidates):
        candidates.insert(0, title)

    ranked = sorted(
        [(index, sentence, _score(sentence, index)) for index, sentence in enumerate(candidates)],
        key=lambda row: row[2],
        reverse=True,
    )
    chosen: list[tuple[int, str, float]] = []
    for index, sentence, score in ranked:
        if _contains_any(sentence, VAGUE_WORDS) and score < 5:
            continue
        if not _unique(chosen, sentence):
            continue
        clipped = sentence[:180].rstrip() + ("…" if len(sentence) > 180 else "")
        chosen.append((index, clipped, score))
        if len(chosen) >= 3:
            break

    causal = [row for row in ranked if _contains_any(row[1], CAUSE_WORDS) and not _contains_any(row[1], VAGUE_WORDS)]
    if causal and not any(_contains_any(sentence, CAUSE_WORDS) for _, sentence, _ in chosen):
        candidate = causal[0]
        clipped = candidate[1][:180].rstrip() + ("…" if len(candidate[1]) > 180 else "")
        replacement = (candidate[0], clipped, candidate[2])
        if len(chosen) < 3:
            chosen.append(replacement)
        else:
            chosen[-1] = replacement

    # The format requires 2-3 sentences when the source actually contains them.
    if len(chosen) < 2:
        for index, sentence, score in sorted(ranked, key=lambda row: row[0]):
            if _unique(chosen, sentence) and not _contains_any(sentence, VAGUE_WORDS):
                clipped = sentence[:180].rstrip() + ("…" if len(sentence) > 180 else "")
                chosen.append((index, clipped, score))
            if len(chosen) >= 2:
                break

    chosen = sorted(chosen[:3], key=lambda row: row[0])
    sentences = [sentence for _, sentence, _ in chosen]
    if not sentences:
        sentences = [title[:150]] if title else ["원문에서 요약 가능한 사실 문장을 찾지 못했습니다."]

    if isinstance(source, (list, tuple, set)):
        source = ", ".join(_text(value) for value in source if _text(value)) or "출처미상"
    summary = " ".join(sentences).strip()
    return f"{summary}\n출처: {source} | 시각: {published}"


def install() -> None:
    from . import summarizer

    summarizer.summarize = summarize_article
