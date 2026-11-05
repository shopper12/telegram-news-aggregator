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
from .crisis_watch import KST, _chat_ids, scan, detect_events
from .market_interpreter import interpret_market

PREFIXES = ("/market", "/risk", "봇 시장", "봇 리스크", "시장 ", "주식 ", "나스닥 ", "코스피 ", "코스닥 ")
KEYWORDS = ("폭락", "급락", "급등", "패닉", "왜 떨어", "지금 팔아", "지금 사", "시장 왜", "시장 상황", "시장 위험")

_SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_MINUTES = 60

def market_context_active(chat_id: str) -> bool:
    info = _SESSIONS.get(str(chat_id)) or {}
    ts = info.get("at")
    return isinstance(ts, datetime) and datetime.now(timezone.utc) - ts < timedelta(minutes=SESSION_MINUTES)

def remember_market_question(chat_id: str, text: str) -> None:
    key = str(chat_id)
    previous = _SESSIONS.get(key) or {}
    history = list(previous.get("history") or []) if market_context_active(key) else []
    history.append(str(text)[:500])
    _SESSIONS[key] = {"at": datetime.now(timezone.utc), "history": history[-6:]}

def is_other_bot_command(text: str) -> bool:
    body = (text or "").strip()
    return body.startswith(("봇 뉴스", "봇 시세", "봇 사주", "봇 생년월일", "봇 도움말", "/start", "뉴스갱신"))

def should_handle(text: str) -> bool:
    text = (text or "").strip()
    if text in ("시장", "봇 시장", "봇 리스크", "/market", "/risk", "주식"):
        return True
    return text.lower().startswith(PREFIXES) or any(token in text for token in KEYWORDS)

def _headlines(now: datetime) -> list[dict[str, str]]:
    """Query news by relevance and credible outlet; dated headlines are leads, not causal proof."""
    queries = (
        "나스닥 반도체 AI 실적 데이터센터 미국증시 when:1d",
        "site:reuters.com Nasdaq semiconductor AI stocks when:1d",
    )
    rows: list[tuple[int, dict[str, str]]] = []
    seen: set[str] = set()
    trust = ("reuters", "로이터", "연합뉴스", "ap news", "associated press",
             "bloomberg", "블룸버그", "financial times", "wall street journal")
    useful = ("ai", "semiconductor", "chip", "nasdaq", "qualcomm", "earnings", "data center",
              "반도체", "나스닥", "퀄컴", "규제", "데이터센터", "실적", "금리")
    for search in queries:
        try:
            url = "https://news.google.com/rss/search?" + urlencode(
                {"q": search, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
            )
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall(".//item")[:30]:
                title, published = item.findtext("title"), item.findtext("pubDate")
                if not title or not published:
                    continue
                try:
                    dt = parsedate_to_datetime(published).astimezone(timezone.utc)
                except (ValueError, TypeError):
                    continue
                age = (now - dt).total_seconds()
                if age < -600 or age > 18 * 3600:
                    continue
                key = " ".join(title.strip().lower().split())
                if key in seen:
                    continue
                seen.add(key)
                term_count = sum(1 for word in useful if word in key)
                if term_count == 0:
                    continue
                score = 12 * any(word in key for word in trust) + 2 * term_count
                score += max(0, 7 - int(age // 3600))
                rows.append((score, {
                    "title": title.strip()[:165],
                    "published_at": dt.isoformat(),
                    "link": str(item.findtext("link") or ""),
                }))
        except Exception as exc:
            print(f"[crisis-chat] headline feed degraded: {type(exc).__name__}")
    rows.sort(key=lambda x: -x[0])
    return [item for _, item in rows[:5]]

def _market_text(data: dict[str, Any]) -> str:
    parts = [f"📊 시장 점검 {data['checked_at']} (KST)",
             "Yahoo 비공식 1분봉 / 제공봉 시각 표시(거래소 무지연 시세 아님). 7분 초과 자료는 제외."]
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
        bar_time = str(row.get("at") or "")
        try:
            bar_time = datetime.fromisoformat(bar_time).astimezone(KST).strftime("%H:%M")
        except (ValueError, TypeError):
            bar_time = "미확인"
        parts.append(f"{row['name']}: {a}, {b} (제공봉 {bar_time} KST)")
    events = detect_events(data)
    if events:
        first = events[0]
        parts.append(f"감지: {first['region']} {first['direction']}, 위험단계 {['정상','주의','경계','긴급'][first['level']]}")
    else:
        parts.append("신규 임계치 충족 없음 (정상이라는 뜻은 아니며, 데이터 부재 가능)")
    return "\n".join(parts)

def _model_answer(question: str, data: dict[str, Any],
                  headlines: list[dict[str, str]]) -> tuple[str | None, str]:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        return None, "미설정"
    model = os.getenv("CRISIS_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    evidence = json.dumps({
        "checked_at": data.get("checked_at"),
        "quotes": {k: {key: v.get(key) for key in
                 ("name", "price", "day_pct", "m5_pct", "m15_pct", "at", "age_sec", "fresh", "open")}
                 for k, v in (data.get("quotes") or {}).items()},
    }, ensure_ascii=False)
    prompt = (
        "너는 근거 기반의 시장위험 전문 애널리스트다. 사용자가 왜 폭락했는지, "
        "자산배분과 보유종목에 어떻게 대응해야 할지 알고 싶어 한다. "
        "절대 시세를 나열하고 '원인 단정 불가'만 말하고 끝내지 마라. "
        "1) 관측 수치와 상대강도 2) 최신 보도에 등장하는 구체적 악재(제목/발행시각), "
        "보도로 확인된 사실과 가능한 해석 구별 3) 채권·금·신용으로 교차검증 "
        "4) 추가 급락/반등을 가를 검증가능한 지표와 조건 "
        "5) 실제 보유종목·평단·비중 모르면 확정적 매매 지시 하지 말고 "
        "레버리지/현금/장기 ETF 경우를 구분한 행동 기준을 한국어로 600~900자 작성. "
        "근거 없이 배경사실 추가하거나 뉴스 제목만으로 원인 확정하지 말 것. "
        "시세는 Yahoo 비공식 분봉이며 최근 봉 표기가 실시간성을 보장하지 않음. "
        "뉴스가 없거나 무관하면 없다고 밝힐 것. "
        f"질문 및 최근 대화: {question}\n시세: {evidence[:10000]}\n"
        f"최근 뉴스: {json.dumps(headlines, ensure_ascii=False)[:4000]}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 1450},
    }
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=payload, timeout=20,
        )
        response.raise_for_status()
        candidates = response.json().get("candidates") or []
        answer = "".join(part.get("text", "") for part in
                         (candidates[0].get("content", {}).get("parts") or [])) if candidates else ""
        return (answer.strip()[:2800], "정상") if answer.strip() else (None, "응답 없음")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        print(f"[crisis-chat] AI HTTP {status} (key not logged)")
        return None, f"HTTP {status}"
    except Exception as exc:
        print(f"[crisis-chat] AI unavailable: {type(exc).__name__}")
        return None, type(exc).__name__

def answer(question: str, chat_id: str) -> str:
    allowed = _chat_ids()
    if allowed and str(chat_id) not in allowed:
        return "이 시장 관제 기능은 등록된 텔레그램 채팅에만 제공됩니다."
    existing = list((_SESSIONS.get(str(chat_id)) or {}).get("history") or [])
    if not existing or existing[-1] != question:
        remember_market_question(chat_id, question)
    history = list((_SESSIONS.get(str(chat_id)) or {}).get("history") or [])
    now = datetime.now(timezone.utc)
    data = scan(now=now)
    headlines = _headlines(now)
    initial = _market_text(data)
    analysis, model_status = _model_answer(
        "최근 대화(순서대로): " + " | ".join(history) + "\n현재 질문: " + question,
        data, headlines,
    )
    if analysis:
        return (initial + "\n\n🧠 AI 분석(시장 데이터·뉴스 확인)\n" + analysis
                + "\n※ 매매 자동 실행 없음")[:3900]
    # An absent/failed API key may not silently reduce the user to a canned sentence.
    objective = interpret_market(question, data, headlines)
    reason = ("모델 인증키가 서버에 설정되지 않음" if model_status == "미설정"
              else f"모델 호출 실패({model_status})")
    return (initial + "\n\n" + objective
            + f"\n⚙️ AI 추가분석 미적용: {reason}. 위 내용은 실제 가격/기사 기반 규칙형 분석."
            + "\n※ 자동주문 없음")[:3900]
