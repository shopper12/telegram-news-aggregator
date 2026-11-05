"""Conversation adapter for the market crisis watcher in the existing Telegram webhook."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests
from .crisis_watch import KST, _chat_ids, scan, detect_events, format_alert

PREFIXES = ("/market", "/risk", "봇 시장", "봇 리스크", "시장 ", "주식 ", "나스닥 ", "코스피 ", "코스닥 ")
KEYWORDS = ("폭락", "급락", "급등", "패닉", "왜 떨어", "지금 팔아", "지금 사", "시장 왜", "시장 상황", "시장 위험")

def should_handle(text: str) -> bool:
    text = (text or "").strip()
    if text in ("시장", "봇 시장", "봇 리스크", "/market", "/risk", "주식"):
        return True
    return text.lower().startswith(PREFIXES) or any(token in text for token in KEYWORDS)

def _headlines(now: datetime) -> list[dict[str, str]]:
    """Fresh headline evidence, not a proof of price causality."""
    query = "미국 나스닥 S&P500 반도체 주식 급락 OR 코스피 급락"
    try:
        url = "https://news.google.com/rss/search?" + urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        result = []
        for item in root.findall(".//item"):
            title, published = item.findtext("title"), item.findtext("pubDate")
            if not title or not published:
                continue
            dt = parsedate_to_datetime(published).astimezone(timezone.utc)
            if dt > now + timedelta(minutes=10) or dt < now - timedelta(hours=8):
                continue
            result.append({"title": title[:160], "published_at": dt.isoformat(), "link": item.findtext("link") or ""})
            if len(result) >= 4:
                break
        return result
    except Exception:
        return []

def _market_text(data: dict[str, Any]) -> str:
    parts = [f"📊 시장 점검 {data['checked_at']} (KST)",
             "Yahoo Finance 비공식 1분봉; 시세 지연·오류 가능. 420초 초과 자료는 판단 제외."]
    for symbol in ("SPY", "QQQ", "SOXX", "NVDA", "^KS11", "^KQ11", "005930.KS", "000660.KS", "TLT", "GLD"):
        row = (data.get("quotes") or {}).get(symbol)
        if not row:
            continue
        if not row.get("fresh") or not row.get("open"):
            parts.append(f"{row.get('name', symbol)}: 장외/지연/조회 불가")
            continue
        daily = row.get("day_pct")
        five = row.get("m5_pct")
        a = f"일간 {daily:+.2f}%" if daily is not None else "일간 미확인"
        b = f"5분 {five:+.2f}%" if five is not None else "5분 미확인"
        parts.append(f"{row['name']}: {a}, {b} (자료 {row.get('age_sec')}초 전)")
    events = detect_events(data)
    if events:
        first = events[0]
        parts.append(f"감지: {first['region']} {first['direction']}, 위험단계 {['정상','주의','경계','긴급'][first['level']]}")
    else:
        parts.append("신규 임계치 충족 없음 (정상이라는 뜻은 아니며, 데이터 부재 가능)")
    return "\n".join(parts)

def _model_answer(question: str, data: dict[str, Any], headlines: list[dict[str, str]]) -> str | None:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        return None
    model = os.getenv("CRISIS_GEMINI_MODEL", "gemini-2.5-flash")
    payload = {
        "contents": [{"parts": [{"text": (
            "당신은 한국어 금융위험 관제 보조자다. 반드시 제공된 시세와 뉴스만 근거로 답변하라. "
            "시세가 stale/closed이면 실시간으로 단정하지 마라. 뉴스 헤드라인은 시장 원인으로 확정하지 말고 "
            "'가능한 설명(미확인)'으로만 제시. 포지션·계좌·매수가를 모르므로 '지금 팔아?'에 확정 주문 지시 금지. "
            "1) 확인된 사실 2) 원인 후보와 근거/불확실성 3) 포지션 위험에 따른 대응조건 4) 다음 확인 지표를 "
            "총 500자 내외로 간결하게 한국어로 설명. 시장 데이터 부재는 부재로 기재. "
            f"질문: {question}\n데이터: {json.dumps(data, ensure_ascii=False)[:11000]}\n"
            f"최근 헤드라인: {json.dumps(headlines, ensure_ascii=False)[:3500]}"
        )}]}],
        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 1100},
    }
    try:
        response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                 headers={"x-goog-api-key": key}, json=payload, timeout=15)
        response.raise_for_status()
        answer = "".join(p.get("text", "") for p in
                         ((response.json().get("candidates") or [{}])[0].get("content") or {}).get("parts", []))
        return answer.strip()[:3000] or None
    except Exception as exc:
        print(f"[crisis-chat] model unavailable: {type(exc).__name__}")
        return None

def answer(question: str, chat_id: str) -> str:
    allowed = _chat_ids()
    if allowed and str(chat_id) not in allowed:
        return "이 시장 관제 기능은 등록된 텔레그램 채팅에만 제공됩니다."
    now = datetime.now(timezone.utc)
    data = scan(now=now)
    initial = _market_text(data)
    headlines = _headlines(now)
    summary = _model_answer(question, data, headlines)
    if summary:
        return (initial + "\n\n🔎 상황 분석\n" + summary + "\n※ 매매 실행은 하지 않습니다.")[:3850]
    links = "\n".join(f"• {h['title']} ({h['published_at'][:16]} UTC)" for h in headlines[:3])
    fallback = "가격 변동의 인과관계는 시세만으로 단정할 수 없습니다."
    if any(t in question for t in ("팔아", "매도", "손절")):
        fallback += " 레버리지·신용/만기와 손실한도부터 확인하고, 즉시 매도 여부는 투자기간·보유비중·현금여력에 따라 판단해야 합니다."
    else:
        fallback += " 지수와 반도체 동반 하락인지, VIX·국채·달러의 움직임과 기업 실적·정책 뉴스를 함께 확인해야 합니다."
    if links:
        fallback += "\n최근 헤드라인(관련성·인과관계 미검증):\n" + links
    return (initial + "\n\n" + fallback + "\n※ 시세 실시간 보장 불가 / 자동 주문 없음")[:3850]
