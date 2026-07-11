from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import re
import time

import requests

from .normalizer import DedupedItem
from .extractor import extract_signals, market_type_from_categories


@dataclass(frozen=True)
class SummaryItem:
    title: str
    body: str
    channels: list[str]
    categories: list[str]
    repeat_count: int
    sectors: list[str]
    keywords: list[str]
    tickers: list[str]
    importance_score: int
    judgment: str
    trade_view: str
    risk: str
    source_urls: list[str] = field(default_factory=list)
    message_dates: list[str] = field(default_factory=list)
    gemini_news_type: str = ""
    gemini_impact: str = ""


SENTENCE_RE = re.compile(r"(?<=[.!?。！？다요음임함됨됨다])\s+|\n+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(조|억|만|%|달러|원|억원|조원|bp|톤|건|명)?", re.IGNORECASE)
PROPER_RE = re.compile(r"[A-Z]{2,10}|[가-힣A-Za-z0-9]+(?:전자|화학|증권|금융|중공업|바이오|에너지|테크|그룹|은행|공사|제약|반도체)")
CAUSE_WORDS = ["때문", "영향", "따라", "전망", "기대", "우려", "확대", "감소", "증가", "급등", "하락", "상승", "수혜", "리스크"]


def _make_title(text: str, max_len: int = 72) -> str:
    cleaned = " ".join(text.replace("\n", " ").split())
    for splitter in [" - ", " | ", " / ", "[", "("]:
        if splitter in cleaned and len(cleaned) > max_len:
            cleaned = cleaned.split(splitter)[0].strip()
            break
    return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"


def _contains_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _split_sentences(text: str) -> list[str]:
    compact = re.sub(r"https?://\S+", "", str(text or ""))
    compact = re.sub(r"\s+", " ", compact).strip()
    if not compact:
        return []
    pieces = [p.strip(" -•·") for p in SENTENCE_RE.split(compact) if p.strip(" -•·")]
    if len(pieces) <= 1 and len(compact) > 120:
        pieces = [compact[i:i + 90].strip() for i in range(0, min(len(compact), 270), 90)]
    return pieces


def _sentence_score(sentence: str) -> int:
    score = 0
    if NUMBER_RE.search(sentence):
        score += 4
    if PROPER_RE.search(sentence):
        score += 3
    if _contains_any(sentence, CAUSE_WORDS):
        score += 3
    if _contains_any(sentence, ["공시", "수주", "계약", "실적", "승인", "허가", "금리", "환율", "연준", "fda"]):
        score += 4
    score += max(0, 3 - abs(len(sentence) - 70) // 40)
    return score


def summarize(article: dict) -> str:
    """Compress title and lead/body into <=3 evidence-focused sentences."""
    title = str(article.get("title") or "").strip()
    body = str(article.get("lead") or article.get("body") or article.get("text") or "").strip()
    source = article.get("source") or article.get("channel") or article.get("channels") or "출처미상"
    published = article.get("published_at") or article.get("message_date") or article.get("date") or "시각미상"

    candidates = _split_sentences(f"{title}. {body}")
    ranked = sorted(enumerate(candidates), key=lambda x: (_sentence_score(x[1]), -x[0]), reverse=True)
    selected: list[str] = []
    seen: set[str] = set()
    for _idx, sentence in ranked:
        normalized = re.sub(r"[^0-9A-Za-z가-힣]+", "", sentence.lower())[:80]
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        selected.append(sentence if len(sentence) <= 150 else sentence[:149] + "…")
        if len(selected) >= 3:
            break

    if not selected and title:
        selected = [title]
    summary = " ".join(selected).strip()
    return f"{summary} (출처: {source}, 시각: {published})" if summary else f"요약 불가 (출처: {source}, 시각: {published})"


def _build_judgment(text: str, repeat_count: int, sectors: list[str], tickers: list[str]) -> str:
    if _contains_any(text, ["수주", "계약", "공급", "납품", "승인", "허가", "공시", "인수", "합병", "실적"]):
        base = "실제 이벤트성 뉴스로 분류. 단순 전망보다 가격 반응 가능성이 높다."
    elif _contains_any(text, ["급등", "상한가", "폭등", "돌파", "신고가"]):
        base = "가격 반응이 이미 나온 뉴스로 분류. 추격 매수보다 눌림·재돌파 확인이 우선이다."
    elif _contains_any(text, ["전망", "기대", "관심", "수혜", "관련주"]):
        base = "테마성 해석 뉴스로 분류. 거래대금 동반 여부 확인 전에는 신뢰도를 낮춰야 한다."
    elif _contains_any(text, ["하락", "급락", "악재", "조사", "제재", "소송", "유상증자"]):
        base = "리스크성 뉴스로 분류. 보유 종목이면 손절선·갭하락 가능성부터 확인해야 한다."
    else:
        base = "정보성 뉴스로 분류. 단독 매매 근거보다는 섹터 강도 확인용으로 보는 게 맞다."

    if repeat_count >= 2:
        base += f" 동일/유사 뉴스 반복 {repeat_count}회."
    if sectors:
        base += f" 관련 섹터: {', '.join(sectors)}."
    if tickers:
        base += f" 언급 티커: {', '.join(tickers[:5])}."
    return base


def _build_trade_view(text: str, importance_score: int, repeat_count: int) -> str:
    if _contains_any(text, ["급등", "상한가", "폭등", "신고가"]):
        return "사후 가격반응 뉴스. 제목 알림용으로만 취급."
    if _contains_any(text, ["수주", "계약", "공급", "승인", "허가", "공시", "실적"]):
        return "실제 이벤트 뉴스. 가격·거래대금 확인 필요."
    if importance_score >= 8:
        return "뉴스 강도 높음. 실시간 가격·거량 확인 필요."
    return "단독 근거 부족. 섹터 동시 반응 확인 필요."


def _build_risk(text: str) -> str:
    if _contains_any(text, ["관련주", "수혜", "기대", "전망"]):
        return "테마 과장 가능성. 실제 매출·공시·수급 확인 필요."
    if _contains_any(text, ["급등", "상한가", "폭등"]):
        return "이미 반영된 뉴스일 수 있음. 고점 추격 리스크 큼."
    if _contains_any(text, ["악재", "제재", "소송", "조사", "유상증자"]):
        return "뉴스가 추가 확산되면 하방 변동성 확대 가능."
    return "뉴스 원문만으로는 가격 반응 지속성 판단 불가. 실시간 차트 확인 필요."


def local_summarize(items: list[DedupedItem], limit: int = 15) -> list[SummaryItem]:
    summaries: list[SummaryItem] = []
    for item in items:
        market_type = market_type_from_categories(item.categories)
        sig = extract_signals(item.text, repeat_count=item.count, market_type=market_type)
        title = _make_title(item.text)
        body = summarize(
            {
                "title": title,
                "body": item.text,
                "channels": ", ".join(item.channel_names),
                "published_at": item.message_dates[0] if item.message_dates else "시각미상",
            }
        )
        summaries.append(
            SummaryItem(
                title=title,
                body=body,
                channels=item.channel_names,
                categories=item.categories,
                repeat_count=item.count,
                sectors=sig.sectors,
                keywords=sig.keywords,
                tickers=sig.tickers,
                importance_score=sig.importance_score,
                judgment=_build_judgment(item.text, item.count, sig.sectors, sig.tickers),
                trade_view=_build_trade_view(item.text, sig.importance_score, item.count),
                risk=_build_risk(item.text),
                source_urls=getattr(item, "message_urls", []),
                message_dates=getattr(item, "message_dates", []),
            )
        )
    return summaries


def _extract_json_array(text: str) -> list[dict] | None:
    cleaned = text.strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(cleaned)
    except Exception:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start:end + 1])
        except Exception:
            return None
    return data if isinstance(data, list) else None


def _gemini_classify_batch(batch: list[SummaryItem], api_key: str, model: str) -> dict[int, tuple[str, str]]:
    rows = [
        {"idx": i + 1, "title": s.title, "body": s.body[:260], "categories": s.categories, "sectors": s.sectors, "tickers": s.tickers}
        for i, s in enumerate(batch)
    ]
    prompt = (
        "다음 뉴스 목록을 JSON 배열로 분류해라. Gemini만 사용하는 분류 엔진이다.\n"
        "각 항목은 반드시 {\"idx\": 번호, \"news_type\": 분류, \"impact_level\": 영향도} 형식이다.\n"
        "news_type 선택지: 공시/확정, 이벤트, 실적, 리스크, 거시, 테마, 가격반응, 정보, 광고/잡음\n"
        "impact_level 선택지: 높음, 중간, 낮음, 확인부족\n"
        "분류 기준:\n"
        "- 공시/확정=공시·잠정실적·IR·전자공시·금감원·거래소 확인\n"
        "- 이벤트=수주·계약·공급·납품·승인·허가·인수·합병·상장·임상·FDA\n"
        "- 실적=매출·영업이익·EPS·가이던스·어닝\n"
        "- 리스크=급락·악재·제재·소송·유상증자·거래정지\n"
        "- 거시=금리·환율·FOMC·CPI·관세·미국 경제지표·반도체 수출규제\n"
        "- 가격반응=급등·상한가·신고가 등 이미 가격 반영\n"
        "- 테마=관련주·수혜·전망·기대 중심\n"
        "- 정보=그 외\n"
        "광고/잡음 분류 추가 기준: 광고 초대 링크만 있는 메시지, 순수 인사말, 이모지+감탄사, 리딩방·추천방·무료방·유료방 광고.\n"
        "경제·시황·종목 내용이 있으면 특수문자나 이모지가 있어도 정상 뉴스로 분류한다.\n"
        "JSON 배열만 반환. 다른 텍스트 없음.\n"
        f"뉴스 목록:\n{json.dumps(rows, ensure_ascii=False)}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.05, "maxOutputTokens": 700, "responseMimeType": "application/json"},
    }
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    raw = "".join(part.get("text", "") for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])).strip()
    parsed = _extract_json_array(raw)
    if not parsed:
        return {}
    out: dict[int, tuple[str, str]] = {}
    for item in parsed:
        try:
            idx = int(item.get("idx"))
            news_type = str(item.get("news_type") or "정보")
            impact_level = str(item.get("impact_level") or "확인부족")
            out[idx] = (news_type, impact_level)
        except Exception:
            continue
    return out


def gemini_classify_if_available(items: list[DedupedItem], api_key: str | None, model: str, limit: int = 15) -> list[SummaryItem]:
    summaries = local_summarize(items, limit=limit)
    if not api_key or not summaries:
        return summaries

    updated = summaries[:]
    try:
        for start in range(0, len(updated), 20):
            batch = updated[start:start + 20]
            classifications = _gemini_classify_batch(batch, api_key, model)
            for local_idx, (news_type, impact_level) in classifications.items():
                pos = start + local_idx - 1
                if 0 <= pos < len(updated):
                    prefix = f"[Gemini분류: {news_type}/{impact_level}] "
                    item = updated[pos]
                    updated[pos] = replace(
                        item,
                        judgment=prefix + item.judgment,
                        trade_view=prefix + item.trade_view,
                        gemini_news_type=news_type,
                        gemini_impact=impact_level,
                    )
            if start + 20 < len(updated):
                time.sleep(1.0)
    except Exception:
        return summaries
    return updated


# 구버전 import 호환. OpenAI는 사용하지 않고 Gemini 분류 엔진으로 라우팅한다.
openai_summarize_if_available = gemini_classify_if_available
