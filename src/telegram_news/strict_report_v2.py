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
MAX_DISPLAY_NEWS = 5
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

BAD_DISPLAY_TICKERS = {
    "IDF", "ESS", "NIM", "GLP", "MSTR", "STRC", "DRAM", "KORU", "SPCX",
}
LOW_VALUE_DISPLAY_WORDS = [
    "레딧", "reddit", "게시물 분석", "언급량", "검색량", "트렌드 분석",
    "아직 상장안한", "상장안한", "상장 안 한", "etf도 가능합니다", "도 가능합니다",
    "미리보기가 되지 않아", "다시 올립니다", "아까 올린", "무료방", "추천방", "리딩방",
]
MARKET_WIDE_KEEP_WORDS = [
    "금리", "환율", "연준", "한은", "fomc", "cpi", "ppi", "고용", "관세", "수출규제",
    "최저임금", "코스피", "코스닥", "나스닥", "유가", "국채", "달러",
]
CONFIRMATION_WORDS = [
    "공시", "수주", "계약", "공급", "납품", "승인", "허가", "실적", "매출", "영업이익",
    "가이던스", "배당", "자사주", "증자", "품목허가", "임상", "fda",
]


def _append_diag(report: str, reason: str) -> str:
    if not report or os.getenv("DEBUG_QUALITY", "0") != "1":
        return report
    diag = f"\nGemini진단: {html.escape(reason, quote=False)}"
    base = report[: MAX_REPORT_CHARS - len(diag) - 20]
    return base + diag


def _clean_title_text(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[#@][\w가-힣_]+", "", text)
    text = re.sub(r"[^0-9A-Za-z가-힣]+", "", text)
    return text.strip()


def _has_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _cluster_text(cluster) -> str:
    best = cluster.best()
    return f"{best.item.title} {best.item.body}"


def _display_symbols(cluster) -> list:
    text = _cluster_text(cluster)
    lower = text.lower()
    out = []
    seen = set()
    for sym in cluster.symbols():
        ticker = sym.ticker.upper().replace(".KS", "").replace(".KQ", "")
        if ticker in BAD_DISPLAY_TICKERS:
            continue
        name = str(sym.name or "")
        name_hit = bool(name and name.lower() in lower)
        kr_code_hit = ticker.isdigit() and re.search(rf"(?<!\d){re.escape(ticker)}(?!\d)", text)
        explicit_us_hit = bool(re.search(rf"(?:\${re.escape(ticker)}|\({re.escape(ticker)}\)|NASDAQ:{re.escape(ticker)}|NYSE:{re.escape(ticker)}|AMEX:{re.escape(ticker)})\b", text, re.IGNORECASE))
        common_korean_us = ticker in {"NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "IBM", "AMD", "AVGO", "PLTR", "INTC", "ORCL", "NFLX", "MU", "SMCI"} and name_hit
        if name_hit or kr_code_hit or explicit_us_hit or common_korean_us:
            if sym.ticker not in seen:
                seen.add(sym.ticker)
                out.append(sym)
    return out[:3]


def _symbol_link_map(selected) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cluster in selected:
        for sym in _display_symbols(cluster):
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


def _is_display_noise(cluster) -> bool:
    best = cluster.best()
    text = _cluster_text(cluster)
    title_clean = _clean_title_text(best.item.title)
    symbols = _display_symbols(cluster)
    no_symbols = not symbols

    if len(title_clean) < 8:
        return True
    if _has_any(text, LOW_VALUE_DISPLAY_WORDS):
        return True
    if best.news_type == "가격반응" and no_symbols:
        return True
    if best.news_type == "테마" and materiality_grade(cluster) in {"B", "C"} and no_symbols:
        return True
    if no_symbols and best.news_type not in {"거시", "리스크"}:
        has_confirmation = _has_any(text, CONFIRMATION_WORDS)
        has_market_keep = _has_any(text, MARKET_WIDE_KEEP_WORDS)
        if not has_confirmation and not has_market_keep:
            return True
    if no_symbols and "이스라엘군" in text and not _has_any(text, MARKET_WIDE_KEEP_WORDS):
        return True
    return False


def _drop_noise(clusters: list) -> list:
    return [cluster for cluster in clusters if not _is_display_noise(cluster)]


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


def _empty_report_lines(now, kind, overview, source_count: int) -> list[str]:
    return [
        html.escape(s.base._header(kind), quote=False),
        html.escape(f"{now:%m/%d %H:%M KST}  ·  이슈 0개", quote=False),
        html.escape(f"📈 {overview}", quote=False),
        "주도: 뚜렷한 주도 섹터 없음",
        "",
        "🔇 이 시간대 주요 이슈 없음",
        html.escape(f"(원문 {source_count}건 검토)", quote=False),
    ]


def _title_only_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count, engine: str) -> str:
    display = _drop_noise(selected)[:MAX_DISPLAY_NEWS]
    if not display and os.getenv("SEND_EMPTY_REPORT", "1") == "0":
        return ""

    if not display:
        lines = _empty_report_lines(now, kind, overview, source_count)
    else:
        lines = [
            html.escape(s.base._header(kind), quote=False),
            html.escape(f"{now:%m/%d %H:%M KST}  ·  이슈 {len(display)}개", quote=False),
            html.escape(f"📈 {overview}", quote=False),
            html.escape(f"주도: {_brief_sector_line(display)}", quote=False),
            "",
        ]
        for cluster in display:
            lines.append(_news_title_html(cluster))
            symbols = _display_symbols(cluster)
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
            "symbols": [{"name": sym.name, "ticker": sym.ticker} for sym in _display_symbols(cluster)],
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
