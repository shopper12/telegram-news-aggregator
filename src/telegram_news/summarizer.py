from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import time

import requests

from .normalizer import DedupedItem
from .extractor import extract_signals


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
    gemini_news_type: str = ""
    gemini_impact: str = ""


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
        return "뉴스 강도 높음. 실시간 가격·거래량 확인 필요."
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
        sig = extract_signals(item.text, repeat_count=item.count)
        summaries.append(
            SummaryItem(
                title=_make_title(item.text),
                body=item.text,
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
    rows = [{"idx": i + 1, "title": s.title, "body": s.body[:220]} for i, s in enumerate(batch)]
    prompt = (
        "다음 뉴스 목록을 JSON 배열로 분류해라.\n"
        "각 항목: {\"idx\": 번호, \"news_type\": 분류, \"impact_level\": 영향도}\n"
        "news_type 선택지: 공시/확정, 이벤트, 실적, 리스크, 거시, 테마, 가격반응, 정보, 광고/잡음\n"
        "impact_level 선택지: 높음, 중간, 낮음, 확인부족\n"
        "분류 기준: 공시/확정=공시·잠정실적·IR·전자공시·금감원, 이벤트=수주·계약·공급·납품·승인·허가·인수·합병·상장·임상·FDA, 실적=매출·영업이익·EPS·가이던스·어닝, 리스크=급락·악재·제재·소송·유상증자·거래정지, 거시=금리·환율·FOMC·CPI·관세·미국 경제지표·반도체 수출규제, 가격반응=급등·상한가·신고가 등 이미 가격 반영, 테마=관련주·수혜·전망·기대, 정보=그 외.\n"
        "광고/잡음 분류 추가 기준:\n"
        "- 광고 초대 링크(t.me/+, t.me/joinchat, bit.ly)만 있고 뉴스 내용이 없는 메시지\n"
        "- 순수 인사말·응원 문구(좋은 하루, 화이팅, 감사합니다 등)\n"
        "- 이모지+감탄사(ㅋㅋ, ㅎㅎ)만으로 구성된 메시지\n"
        "- 리딩방·추천방·무료방·유료방 광고\n"
        "이모지(🔴, 📌, ▶ 등)나 특수문자가 있어도 경제·시황·종목 내용이 있으면 정상 뉴스로 분류한다.\n"
        "뉴스 URL(naver.com, hankyung.com, mk.co.kr, yna.co.kr, reuters.com, bloomberg.com 등) 또는 t.me/채널/글번호 원문 링크가 있으면 정상 뉴스로 분류한다. 단 t.me/+ 초대 링크는 원문 링크가 아니다.\n"
        "JSON 배열만 반환. 다른 텍스트 없음.\n"
        f"뉴스 목록:\n{json.dumps(rows, ensure_ascii=False)}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 600, "responseMimeType": "application/json"},
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


def gemini_classify_if_available(
    items: list[DedupedItem],
    api_key: str | None,
    model: str,
    limit: int = 15,
) -> list[SummaryItem]:
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


# 구버전 import 호환. app.py는 gemini_classify_if_available를 직접 사용한다.
openai_summarize_if_available = gemini_classify_if_available
