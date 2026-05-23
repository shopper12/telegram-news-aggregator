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
    diag = f"\nGemini진단: {html.escape(reason, quote=False)}"
    base = report[: MAX_REPORT_CHARS - len(diag) - 20]
    return base + diag


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
    if not symbols:
        return "직접 언급 종목 없음"
    return ", ".join(
        f'<a href="{html.escape(s.base._quote_url(sym), quote=True)}">{html.escape(sym.name + "(" + sym.ticker + ")", quote=False)}</a>'
        for sym in symbols
    )


def _brief_market_view(selected) -> str:
    if not selected:
        return "뉴스 기준 주도 이슈 약함"

    type_counts = Counter(cluster.best().news_type for cluster in selected)
    risk_count = type_counts.get("리스크", 0) + type_counts.get("거시", 0)
    core_count = type_counts.get("공시/확정", 0) + type_counts.get("이벤트", 0) + type_counts.get("실적", 0)
    watch_count = type_counts.get("가격반응", 0) + type_counts.get("테마", 0) + type_counts.get("정보", 0)

    if risk_count > core_count:
        return "거시·리스크 변수 우세"
    if core_count >= 2:
        return "확정·이벤트성 뉴스 우세"
    if watch_count > core_count:
        return "가격반응·테마성 뉴스 혼재"
    return "선별 뉴스 중심"


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
    lines = [
        html.escape(s.base._header(kind), quote=False),
        s.base.DIVIDER,
        html.escape(f"{now:%m/%d %H:%M KST} | 최근 {hours}h | 제목 {len(selected)}개 | 기준 {s.MATERIALITY_THRESHOLD}", quote=False),
        html.escape(f"시장: {overview}", quote=False),
        html.escape(f"시황: {_brief_market_view(selected)}", quote=False),
        html.escape(f"주요 섹터: {_brief_sector_line(selected)}", quote=False),
        "",
    ]

    if not selected:
        lines.append("주요 뉴스: 기준 통과 없음")
    else:
        lines.append("📌 뉴스 제목")
        for idx, cluster in enumerate(selected, 1):
            best = cluster.best()
            label = f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}][{best.news_type}] {s.base._short(best.item.title, 95)}"
            lines.append(_html_linkify_text(label, [cluster]))
            symbols = cluster.symbols()
            if symbols:
                lines.append("   종목: " + _related_html(symbols))

    lines.append("")
    lines.append(html.escape(s._quality_note(engine, rule, source_count, stock_count, blocked, selected, pre_gate_count), quote=False))
    report = "\n".join(lines)
    return report[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report


def _gemini_title_order(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count):
    """Gemini는 요약이 아니라 제목 순서 재정렬/중복검토에만 사용한다. 실패해도 로컬 제목 알림으로 충분히 동작한다."""
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
        "새 제목을 만들거나 문장을 바꾸지 않는다. 결과는 JSON 배열만 출력한다. 예: [2,1,3]\n"
        f"입력:{json.dumps(issues, ensure_ascii=False)}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 120},
    }
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
        return _title_only_report(
            now=now,
            kind=kind,
            hours=hours,
            selected=reordered,
            stock_count=stock_count,
            blocked=blocked,
            rule=rule,
            overview=overview,
            source_count=len(summaries),
            pre_gate_count=pre_gate_count,
            engine="Gemini제목정렬",
        )

    local = _title_only_report(
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
        engine="로컬제목엔진",
    )
    return _append_diag(local, reason)
