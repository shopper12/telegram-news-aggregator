from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
import json
import os
import re

import requests

from .summarizer import SummaryItem
from . import report as base
from .strict_quality import materiality_score, materiality_grade, strict_filter, MATERIALITY_THRESHOLD
from .noise_patterns import NOISE_WORDS, ADVISORY_WORDS, REPOST_WORDS

MAX_NEWS = 3
MAX_REPORT_CHARS = 2300
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
AMBIGUOUS_US_TICKERS = {"IDF", "GLP", "NIM", "ESS", "BIO", "NET", "AI", "EV", "DRAM", "KORU", "SPCX", "MSTR", "STRC"}


def _type_counts(clusters):
    counter = Counter()
    for cluster in clusters:
        counter[cluster.best().news_type] += 1
    return counter


def _quality_note(engine: str, rule: str, source_count: int, stock_count: int, blocked: int, selected, pre_gate_count: int) -> str:
    type_text = ", ".join(f"{k}{v}" for k, v in _type_counts(selected).most_common(4)) or "없음"
    return f"검증: {engine} · {rule} · 원문 {source_count} · 후보 {stock_count} · 제외 {blocked} · 게이트전 {pre_gate_count} · 통과 {len(selected)} · 유형 {type_text}"


def _has_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _item_text(item: SummaryItem) -> str:
    return f"{item.title} {item.body}"


def _strict_blocked_item(item: SummaryItem) -> bool:
    if base._blocked(item):
        return True
    text = _item_text(item)
    lower = text.lower()
    if _has_any(lower, REPOST_WORDS):
        return True
    if _has_any(lower, ADVISORY_WORDS) and not _has_any(lower, base.OFFICIAL_WORDS + base.EVENT_WORDS + base.MACRO_WORDS):
        return True
    return _has_any(lower, [w for w in NOISE_WORDS if w not in ADVISORY_WORDS])


def _explicit_ticker(text: str, ticker: str) -> bool:
    return bool(re.search(rf"(?:\${re.escape(ticker)}|\({re.escape(ticker)}\)|NASDAQ:{re.escape(ticker)}|NYSE:{re.escape(ticker)}|AMEX:{re.escape(ticker)})\b", text, re.IGNORECASE))


def _clean_symbols(news):
    text = _item_text(news.item)
    cleaned = []
    seen = set()
    for sym in news.symbols:
        base_ticker = sym.ticker.upper().replace(".KS", "").replace(".KQ", "")
        if base_ticker in AMBIGUOUS_US_TICKERS and not _explicit_ticker(text, base_ticker):
            continue
        if sym.ticker not in seen:
            cleaned.append(sym)
            seen.add(sym.ticker)
    if len(cleaned) == len(news.symbols):
        return news
    return replace(news, symbols=cleaned)


def _select_strict(items: list[SummaryItem]):
    stock = [x for x in items if base._stock_candidate(x) and not _strict_blocked_item(x)]
    blocked = len([x for x in items if base._blocked(x) or _strict_blocked_item(x)])
    cache = {}
    scored = [_clean_symbols(base._score_item(x, cache)) for x in stock]
    scored = [x for x in scored if x.news_type != "광고/잡음" and x.score >= 50]
    clusters = base._cluster(scored)
    selected = strict_filter(clusters)
    if not selected:
        rule = "엄격: 중요도 통과 없음"
    elif len(clusters) > len(selected):
        rule = "엄격: 중요 이슈만 선별"
    else:
        rule = "엄격"
    return selected, len(stock), blocked, rule, len(clusters)


def _cluster_payload(clusters):
    payload = []
    for idx, cluster in enumerate(clusters, 1):
        best = cluster.best()
        payload.append({
            "rank": idx,
            "materiality_score": materiality_score(cluster),
            "materiality_grade": materiality_grade(cluster),
            "raw_score": cluster.score(),
            "type": best.news_type,
            "title": best.item.title,
            "body": base._short(best.item.body, 360),
            "sectors": cluster.sectors(),
            "reasons": best.reasons,
            "channels": sorted({c for n in cluster.items for c in n.item.channels}),
            "external_check": {"level": best.impact.impact_level, "result_count": best.impact.result_count, "latest_title": best.impact.latest_title},
            "symbols": [{"name": s.name, "ticker": s.ticker, "url": base._quote_url(s)} for s in cluster.symbols()],
        })
    return payload


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


def _audit_text(text: str, selected) -> tuple[bool, str]:
    lower = text.lower()
    if any(word in lower for word in base.BLOCK_WORDS):
        return False, "crypto_leak"
    banned = ["\uc9c4\uc785", "\uc190\uc808", "\ubaa9\ud45c\uac00", "\ub9e4\ub9e4", "\ucd94\ucc9c"]
    if any(word in text for word in banned):
        return False, "trading_language_leak"
    if selected and "중요" not in text:
        return False, "no_importance_label"
    item_count = len(re.findall(r"\n\d+\)", "\n" + text))
    if item_count > len(selected):
        return False, "invented_extra_issue"
    return True, "pass"


def _gemini_strict_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not selected:
        return None
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "header": base._header(kind),
        "time_kst": now.strftime("%m/%d %H:%M KST"),
        "hours": hours,
        "market_overview": overview,
        "quality": {"gate": "B+ 이상만 통과", "threshold": MATERIALITY_THRESHOLD, "source_count": source_count, "stock_candidate_count": stock_count, "excluded_count": blocked, "pre_gate_issue_count": pre_gate_count, "selected_issue_count": len(selected)},
        "issues": _cluster_payload(selected),
    }
    prompt = (
        "너는 한국 주식시장 뉴스 데스크이자 품질감사관이다. 입력 JSON의 사실만 사용한다.\n"
        "출력 전에 스스로 점검한다: 중요도 낮은 이슈, 단순 사후 가격반응, 테마성 해석, 입력에 없는 종목 확장을 모두 제거한다.\n"
        "중요도 B+ 미만은 쓰지 않는다. 약하면 '중요 뉴스 없음'이라고 쓴다.\n"
        "코인과 가상자산은 금지한다. 가격 레벨이나 거래 지시 표현도 금지한다.\n"
        "symbols에 있는 직접 언급 종목만 쓴다. 종목이 없는 거시경제 뉴스는 중요하면 유지한다.\n"
        "반드시 JSON 객체만 반환한다. 키는 report, audit, prompt_update 세 개다.\n"
        "audit에는 pass, score, removed_low_value_count, reason을 넣는다. score 85 미만이면 pass=false.\n"
        "report 형식은 다음과 같다.\n"
        "제목\n━━━━━━━━━━━━━━\n시간 | 최근 n시간 | 이슈 n개\n시장: ...\n시황: 1문장\n주요 섹터: 1문장\n\n"
        "📌 핵심 이슈\n1) [중요도점수/등급] 제목\n   요지: 왜 중요한지 1문장\n   영향: 섹터 또는 시장 영향 1문장\n   관련: 종목명(티커) URL 또는 직접 언급 종목 없음\n\n"
        f"검증: Gemini({model}) · 자체감사 · ...\n"
        "전체 report는 2100자 이하.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000, "responseMimeType": "application/json"}}
    try:
        response = requests.post(url, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json=body, timeout=25)
        response.raise_for_status()
        data = response.json()
        raw = "".join(part.get("text", "") for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])).strip()
        parsed = _extract_json_object(raw)
        if not parsed:
            return None
        audit = parsed.get("audit") or {}
        if audit.get("pass") is False or int(audit.get("score") or 0) < 85:
            return None
        text = str(parsed.get("report") or "").strip()
        ok, reason = _audit_text(text, selected)
        if not ok:
            return None
        if "검증:" not in text:
            text += "\n\n" + _quality_note(f"Gemini({model})", rule, source_count, stock_count, blocked, selected, pre_gate_count)
        if "자체감사" not in text:
            text += f" · 자체감사 {audit.get('score', 'NA')}/100"
        return text[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(text) > MAX_REPORT_CHARS else text
    except Exception:
        return None


def _local_strict_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count):
    lines = [base._header(kind), base.DIVIDER, f"{now:%m/%d %H:%M KST} | 최근 {hours}h | 이슈 {len(selected)}개 | 기준 {MATERIALITY_THRESHOLD}", f"시장: {overview}", f"시황: {base._market_view(selected) if selected else '중요도 게이트를 통과한 뉴스 없음. 억지로 이슈를 만들지 않음.'}", f"주요 섹터: {base._sector_sentence(selected) if selected else '중요도 기준 통과 섹터 없음.'}", ""]
    if not selected:
        lines.append("주요 뉴스: 중요도 게이트 통과 없음")
    else:
        lines.append("📌 핵심 이슈")
        for idx, cluster in enumerate(selected, 1):
            best = cluster.best()
            lines.append(f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}] {base._short(best.item.title, 72)}")
            lines.append(f"   요지: {base.TYPE_MEANING.get(best.news_type, '중요 이슈')} · 중요도 게이트 통과")
            lines.append(f"   영향: {base._issue_impact(cluster)}")
            lines.append(f"   판단: 유형={best.news_type}, 등급={materiality_grade(cluster)}, 근거={', '.join(best.reasons[:4])}")
            lines.append(f"   관련: {base._links(cluster.symbols())}")
    lines.append("")
    lines.append(_quality_note("로컬엄격엔진", rule, source_count, stock_count, blocked, selected, pre_gate_count))
    report = "\n".join(lines)
    return report[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    selected, stock_count, blocked, rule, pre_gate_count = _select_strict(summaries)
    overview = base._overview()
    gemini = _gemini_strict_report(now=now, kind=kind, hours=hours, selected=selected, stock_count=stock_count, blocked=blocked, rule=rule, overview=overview, source_count=len(summaries), pre_gate_count=pre_gate_count)
    if gemini:
        return gemini
    return _local_strict_report(now=now, kind=kind, hours=hours, selected=selected, stock_count=stock_count, blocked=blocked, rule=rule, overview=overview, source_count=len(summaries), pre_gate_count=pre_gate_count)
