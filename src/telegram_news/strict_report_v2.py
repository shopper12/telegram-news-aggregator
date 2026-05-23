from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .summarizer import SummaryItem
from . import strict_report as s
from .strict_quality import materiality_score, materiality_grade

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


def _coerce_report_text(raw: str) -> str:
    text = raw.strip()
    parsed = _extract_json_object(text)
    if isinstance(parsed, dict):
        report = parsed.get("report")
        if report:
            return str(report).strip()
    text = re.sub(r"^```(?:text|markdown|md)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    if "report" in text[:80].lower():
        text = re.sub(r'^\s*["\']?report["\']?\s*[:=]\s*', "", text, flags=re.IGNORECASE).strip()
    return text


def _append_diag(report: str, reason: str) -> str:
    diag = f"\nGemini진단: {reason}"
    base = report[: MAX_REPORT_CHARS - len(diag) - 20]
    return base + diag


def _ensure_importance_labels(text: str, selected) -> str:
    if not selected or "중요" in text:
        return text
    summary = ", ".join(
        f"{idx}:{materiality_score(cluster)}/{materiality_grade(cluster)}"
        for idx, cluster in enumerate(selected, 1)
    )
    return text.rstrip() + f"\n중요도: {summary}"


def _audit_text_v2(text: str, selected) -> tuple[bool, str]:
    lower = text.lower()
    if any(word in lower for word in s.base.BLOCK_WORDS):
        return False, "crypto_leak"
    banned = ["진입", "손절", "목표가", "매매", "추천"]
    if any(word in text for word in banned):
        return False, "trading_language_leak"
    item_count = len(re.findall(r"\n\d+\)", "\n" + text))
    if item_count > len(selected):
        return False, "invented_extra_issue"
    return True, "pass"


def _symbol_link_map(selected) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cluster in selected:
        for sym in cluster.symbols():
            url = s.base._quote_url(sym)
            display = f"{sym.name}({sym.ticker})"
            mapping[display] = url
            mapping[sym.name] = url
    return mapping


def _html_linkify_report(text: str, selected) -> str:
    mapping = _symbol_link_map(selected)
    if not mapping:
        return html.escape(text, quote=False)

    # 긴 이름부터 치환해야 삼성전자보다 삼성 등이 먼저 잡히는 문제를 줄일 수 있다.
    keys = sorted(mapping.keys(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in keys))
    out: list[str] = []
    pos = 0
    linked: set[str] = set()

    for match in pattern.finditer(text):
        key = match.group(0)
        out.append(html.escape(text[pos:match.start()], quote=False))
        if key in linked:
            out.append(html.escape(key, quote=False))
        else:
            linked.add(key)
            url = html.escape(mapping[key], quote=True)
            label = html.escape(key, quote=False)
            out.append(f'<a href="{url}">{label}</a>')
        pos = match.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def _related_html(symbols) -> str:
    if not symbols:
        return "직접 언급 종목 없음"
    return ", ".join(
        f'<a href="{html.escape(s.base._quote_url(sym), quote=True)}">{html.escape(sym.name + "(" + sym.ticker + ")", quote=False)}</a>'
        for sym in symbols
    )


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
        "너는 한국 주식시장 뉴스 데스크다. 입력 JSON의 사실만 사용한다.\n"
        "중요도 B+ 이상으로 선별된 이슈만 들어오며, 새 이슈를 만들면 안 된다.\n"
        "각 핵심 이슈 번호에는 반드시 [중요도점수/등급] 형식을 붙인다. 예: 1) [92/A] 제목\n"
        "단순 사후 가격반응, 테마성 해석, 입력에 없는 종목 확장은 금지한다.\n"
        "코인/가상자산/매매전략/추천/진입가/손절가/목표가는 금지한다.\n"
        "symbols에 있는 직접 언급 종목만 쓴다.\n"
        "종목 URL은 쓰지 말고 종목명(티커)만 쓴다. 링크는 시스템이 나중에 자동으로 건다.\n"
        "JSON으로 답하지 말고 텔레그램에 그대로 보낼 plain text report만 출력한다.\n"
        "형식:\n"
        "제목\n━━━━━━━━━━━━━━\n시간 | 최근 n시간 | 이슈 n개\n시장: ...\n시황: 1문장\n주요 섹터: 1문장\n\n"
        "📌 핵심 이슈\n1) [중요도점수/등급] 제목\n   요지: 왜 중요한지 1문장\n   영향: 섹터 또는 시장 영향 1문장\n   관련: 종목명(티커) 또는 직접 언급 종목 없음\n\n"
        f"검증: Gemini({model}) · 로컬사후감사 · ...\n"
        "전체 report는 2100자 이하.\n\n"
        f"입력 JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 900,
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
    if not raw:
        return None, "Gemini 응답 텍스트 빈값"

    text = _coerce_report_text(raw)
    if not text:
        return None, "Gemini report 빈값"

    text = _ensure_importance_labels(text, selected)
    ok, reason = _audit_text_v2(text, selected)
    if not ok:
        return None, f"로컬 사후감사 탈락: {reason}"

    if "검증:" not in text:
        text += "\n\n" + s._quality_note(f"Gemini({model})", rule, source_count, stock_count, blocked, selected, pre_gate_count)
    if "로컬사후감사" not in text:
        text += " · 로컬사후감사 통과"
    text = text[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(text) > MAX_REPORT_CHARS else text
    return _html_linkify_report(text, selected), "Gemini 성공"


def _local_html_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count):
    lines = [
        html.escape(s.base._header(kind), quote=False),
        s.base.DIVIDER,
        html.escape(f"{now:%m/%d %H:%M KST} | 최근 {hours}h | 이슈 {len(selected)}개 | 기준 {s.MATERIALITY_THRESHOLD}", quote=False),
        html.escape(f"시장: {overview}", quote=False),
        html.escape(f"시황: {s.base._market_view(selected) if selected else '중요도 게이트를 통과한 뉴스 없음. 억지로 이슈를 만들지 않음.'}", quote=False),
        html.escape(f"주요 섹터: {s.base._sector_sentence(selected) if selected else '중요도 기준 통과 섹터 없음.'}", quote=False),
        "",
    ]
    if not selected:
        lines.append("주요 뉴스: 중요도 게이트 통과 없음")
    else:
        lines.append("📌 핵심 이슈")
        for idx, cluster in enumerate(selected, 1):
            best = cluster.best()
            lines.append(html.escape(f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}] {s.base._short(best.item.title, 72)}", quote=False))
            lines.append(html.escape(f"   요지: {s.base.TYPE_MEANING.get(best.news_type, '중요 이슈')} · 중요도 게이트 통과", quote=False))
            lines.append(html.escape(f"   영향: {s.base._issue_impact(cluster)}", quote=False))
            lines.append(html.escape(f"   판단: 유형={best.news_type}, 등급={materiality_grade(cluster)}, 근거={', '.join(best.reasons[:4])}", quote=False))
            lines.append("   관련: " + _related_html(cluster.symbols()))
    lines.append("")
    lines.append(html.escape(s._quality_note("로컬엄격엔진", rule, source_count, stock_count, blocked, selected, pre_gate_count), quote=False))
    report = "\n".join(lines)
    return report[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report


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

    local = _local_html_report(
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
