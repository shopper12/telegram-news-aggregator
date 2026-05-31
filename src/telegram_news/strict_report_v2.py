from __future__ import annotations

from collections import Counter
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


def _append_diag(report: str, reason: str) -> str:
    if not report or os.getenv("DEBUG_QUALITY", "0") != "1":
        return report
    diag = f"\nGemini진단: {html.escape(reason, quote=False)}"
    base = report[: MAX_REPORT_CHARS - len(diag) - 20]
    return base + diag


def _symbol_link_map(selected) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cluster in selected:
        for sym in cluster.symbols():
            url = s.base._quote_url(sym)
            display = f"{sym.name}({sym.ticker})"
            mapping[display] = url
            mapping[sym.name] = url
    return mapping


def _html_linkify_text(text: str, selected) -> str:
    mapping = _symbol_link_map(selected)
    if not mapping:
        return html.escape(text, quote=False)
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
            out.append(f'<a href="{html.escape(mapping[key], quote=True)}">{html.escape(key, quote=False)}</a>')
        pos = match.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def _related_html(symbols) -> str:
    return ", ".join(
        f'<a href="{html.escape(s.base._quote_url(sym), quote=True)}">{html.escape(sym.name + "(" + sym.ticker + ")", quote=False)}</a>'
        for sym in symbols
    )


def _source_url(cluster) -> str | None:
    best = cluster.best()
    urls = getattr(best.item, "source_urls", []) or []
    for url in urls:
        if isinstance(url, str) and url.startswith("https://t.me/"):
            return url
    return None


def _action_emoji(cluster) -> str:
    news_type = cluster.best().news_type
    grade = materiality_grade(cluster)
    if news_type in {"가격반응", "리스크"} or grade == "C":
        return "🔴"
    if news_type in {"공시/확정", "이벤트", "실적"} and grade in {"A", "B+", "B"}:
        return "🟢"
    return "🟡"


def _news_title_html(cluster) -> str:
    best = cluster.best()
    score_tag = f"[{materiality_score(cluster)}·{materiality_grade(cluster)}]"
    title_text = f"{_action_emoji(cluster)} {s.base._short(best.item.title, 90)} {score_tag}"
    url = _source_url(cluster)
    if url:
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(title_text, quote=False)}</a>'
    return _html_linkify_text(title_text, [cluster])


def _drop_noise(clusters: list) -> list:
    filtered = []
    for cluster in clusters:
        best = cluster.best()
        no_symbols = not cluster.symbols()
        is_price_noise = best.news_type == "가격반응" and no_symbols
        is_theme_noise = best.news_type == "테마" and materiality_grade(cluster) in {"B", "C"} and no_symbols
        if is_price_noise or is_theme_noise:
            continue
        filtered.append(cluster)
    return filtered if filtered else clusters


def _brief_sector_line(selected) -> str:
    if not selected:
        return "뚜렷한 주도 섹터 없음"
    counter: Counter[str] = Counter()
    for cluster in selected:
        weight = max(1, materiality_score(cluster) // 20)
        for sector in cluster.sectors():
            counter[sector] += weight
    if not counter:
        return "섹터 불명확"
    return " > ".join(sector for sector, _ in counter.most_common(4))


def _title_only_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count, engine: str) -> str:
    if not selected and os.getenv("SEND_EMPTY_REPORT", "1") == "0":
        return ""
    display = _drop_noise(selected)
    lines = [
        html.escape(s.base._header(kind), quote=False),
        html.escape(f"{now:%m/%d %H:%M KST}  ·  이슈 {len(display)}개", quote=False),
        html.escape(f"📈 {overview}", quote=False),
        html.escape(f"주도: {_brief_sector_line(display)}", quote=False),
        "",
    ]
    if not selected:
        lines.append("🔇 이 시간대 주요 이슈 없음")
        lines.append(html.escape(f"(원문 {source_count}건 검토)", quote=False))
    else:
        for cluster in display:
            lines.append(_news_title_html(cluster))
            symbols = cluster.symbols()
            if symbols:
                lines.append("📎 " + _related_html(symbols))
            lines.append("")
    if os.getenv("DEBUG_QUALITY", "0") == "1":
        lines.append(html.escape(s._quality_note(engine, rule, source_count, stock_count, blocked, selected, pre_gate_count), quote=False))
    else:
        lines.append(html.escape(f"🤖 {engine} · 원문 {source_count}건 → {len(display)}개 선별", quote=False))
    report = "\n".join(lines).strip()
    return report[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report


def _gemini_title_order(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY 없음"
    if not selected:
        return None, "기준 통과 이슈 0개라 Gemini 호출 생략"
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    issues = []
    for idx, cluster in enumerate(selected, 1):
        best = cluster.best()
        issues.append({
            "id": idx,
            "score": materiality_score(cluster),
            "grade": materiality_grade(cluster),
            "type": best.news_type,
            "title": best.item.title,
            "symbols": [{"name": sym.name, "ticker": sym.ticker} for sym in cluster.symbols()],
        })
    prompt = (
        "너는 뉴스 제목 선별 보조 엔진이다. 요약하지 말고 입력된 제목 id 순서만 중요도순으로 재정렬한다.\n"
        "가격반응·테마성·종목 없음 이슈는 뒤로 보낸다. 공시·계약·실적·리스크·거시 이슈를 앞에 둔다.\n"
        "새 제목을 만들거나 문장을 바꾸지 않는다. 결과는 JSON 배열만 출력한다. 예: [2,1,3]\n"
        f"입력:{json.dumps(issues, ensure_ascii=False)}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.0, "maxOutputTokens": 120}}
    try:
        response = requests.post(url, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json=body, timeout=20)
    except Exception as exc:
        return None, f"Gemini 요청 예외: {type(exc).__name__}: {exc}"
    if response.status_code != 200:
        return None, f"Gemini HTTP {response.status_code}: {' '.join(response.text.split())[:180]}"
    try:
        data = response.json()
        raw = "".join(part.get("text", "") for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])).strip()
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        order = json.loads(raw)
        if not isinstance(order, list):
            return None, "Gemini 순서 응답이 배열이 아님"
        by_id = {i + 1: cluster for i, cluster in enumerate(selected)}
        reordered = [by_id[int(x)] for x in order if int(x) in by_id]
        for cluster in selected:
            if cluster not in reordered:
                reordered.append(cluster)
        return reordered[:len(selected)], "Gemini 제목순서 성공"
    except Exception as exc:
        return None, f"Gemini 제목순서 파싱 실패: {type(exc).__name__}"


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    selected, stock_count, blocked, rule, pre_gate_count = s._select_strict(summaries)
    overview = s.base._overview()
    if not selected and os.getenv("SEND_EMPTY_REPORT", "1") == "0":
        return ""
    reordered, reason = _gemini_title_order(
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
    if reordered:
        return _title_only_report(now=now, kind=kind, hours=hours, selected=reordered, stock_count=stock_count, blocked=blocked, rule=rule, overview=overview, source_count=len(summaries), pre_gate_count=pre_gate_count, engine="Gemini제목정렬")
    local = _title_only_report(now=now, kind=kind, hours=hours, selected=selected, stock_count=stock_count, blocked=blocked, rule=rule, overview=overview, source_count=len(summaries), pre_gate_count=pre_gate_count, engine="로컬제목엔진")
    return _append_diag(local, reason)
