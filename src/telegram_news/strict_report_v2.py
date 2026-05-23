from __future__ import annotations

import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .summarizer import SummaryItem
from . import strict_report as s

MAX_REPORT_CHARS = 2300
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def _extract_json_object(text: str):
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except Exception:
                return None
    return None


def _append_diag(report: str, reason: str) -> str:
    diag = f"\nGemini진단: {reason}"
    base = report[: MAX_REPORT_CHARS - len(diag) - 20]
    return base + diag


def _gemini_with_diagnostics(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY 없음"
    if not selected:
        return None, "중요도 게이트 통과 이슈 0개라 Gemini 호출 생략"

    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "header": s.base._header(kind),
        "time_kst": now.strftime("%m/%d %H:%M KST"),
        "hours": hours,
        "market_overview": overview,
        "quality": {
            "gate": "B+ 이상만 통과",
            "threshold": s.MATERIALITY_THRESHOLD,
            "source_count": source_count,
            "stock_candidate_count": stock_count,
            "excluded_count": blocked,
            "pre_gate_issue_count": pre_gate_count,
            "selected_issue_count": len(selected),
            "model": model,
        },
        "issues": s._cluster_payload(selected),
    }
    prompt = (
        "너는 한국 주식시장 뉴스 데스크이자 품질감사관이다. 입력 JSON의 사실만 사용한다.\n"
        "중요도 B+ 미만, 단순 사후 가격반응, 테마성 해석, 입력에 없는 종목 확장을 제거한다.\n"
        "코인/가상자산/매매전략/추천/진입가/손절가/목표가는 금지한다.\n"
        "반드시 JSON 객체만 반환한다. 키는 report, audit, prompt_update 세 개다.\n"
        "audit에는 pass, score, removed_low_value_count, reason을 넣는다. score 80 미만이면 pass=false.\n"
        "report 형식:\n"
        "제목\n━━━━━━━━━━━━━━\n시간 | 최근 n시간 | 이슈 n개\n시장: ...\n시황: 1문장\n주요 섹터: 1문장\n\n"
        "📌 핵심 이슈\n1) [중요도점수/등급] 제목\n   요지: 왜 중요한지 1문장\n   영향: 섹터 또는 시장 영향 1문장\n   관련: 종목명(티커) URL 또는 직접 언급 종목 없음\n\n"
        f"검증: Gemini({model}) · 자체감사 · ...\n"
        "전체 report는 2100자 이하.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1000,
            "responseMimeType": "application/json",
        },
    }
    try:
        response = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=25,
        )
    except Exception as exc:
        return None, f"Gemini 요청 예외: {type(exc).__name__}: {exc}"

    if response.status_code != 200:
        short = " ".join(response.text.split())[:260]
        return None, f"Gemini HTTP {response.status_code}: {short}"

    try:
        data = response.json()
    except Exception as exc:
        return None, f"Gemini 응답 JSON 파싱 실패: {type(exc).__name__}"

    raw = "".join(
        part.get("text", "")
        for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    ).strip()
    parsed = _extract_json_object(raw)
    if not parsed:
        return None, f"Gemini report JSON 파싱 실패: {raw[:160]}"

    audit = parsed.get("audit") or {}
    try:
        audit_score = int(audit.get("score") or 0)
    except Exception:
        audit_score = 0
    if audit.get("pass") is False or audit_score < 80:
        return None, f"Gemini 자체감사 탈락: score={audit_score}, reason={audit.get('reason')}"

    text = str(parsed.get("report") or "").strip()
    if not text:
        return None, "Gemini report 빈값"

    ok, reason = s._audit_text(text, selected)
    if not ok:
        return None, f"로컬 사후감사 탈락: {reason}"

    if "검증:" not in text:
        text += "\n\n" + s._quality_note(f"Gemini({model})", rule, source_count, stock_count, blocked, selected, pre_gate_count)
    if "자체감사" not in text:
        text += f" · 자체감사 {audit_score}/100"
    return text[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(text) > MAX_REPORT_CHARS else text, "Gemini 성공"


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    selected, stock_count, blocked, rule, pre_gate_count = s._select_strict(summaries)
    overview = s.base._overview()

    gemini, reason = _gemini_with_diagnostics(
        now=now,
        kind=kind,
        hours=hours,
        selected=selected,
        stock_count=stock_count,
        blocked=blocked,
        rule=rule,
        overview=overview,
        source_count=len(summaries),
        pre_gate_count=pre_gate_count,
    )
    if gemini:
        return gemini

    local = s._local_strict_report(
        now=now,
        kind=kind,
        hours=hours,
        selected=selected,
        stock_count=stock_count,
        blocked=blocked,
        rule=rule,
        overview=overview,
        source_count=len(summaries),
        pre_gate_count=pre_gate_count,
    )
    return _append_diag(local, reason)
