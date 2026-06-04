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
from .noise_patterns import LOW_VALUE_WORDS
from .market_data import get_market_context

MAX_REPORT_CHARS = 2300
MAX_DISPLAY_NEWS = 5
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

BAD_DISPLAY_TICKERS = {
    "IDF", "ESS", "NIM", "GLP", "MSTR", "STRC", "DRAM", "KORU", "SPCX",
}
LOW_VALUE_DISPLAY_WORDS = LOW_VALUE_WORDS + [
    "아직 상장안한", "상장안한", "상장 안 한", "etf도 가능합니다", "도 가능합니다",
    "미리보기가 되지 않아", "다시 올립니다", "아까 올린", "무료방", "추천방", "리딩방",
]
MARKET_WIDE_KEEP_WORDS = [
    "금리", "환율", "연준", "한은", "fomc", "cpi", "ppi", "고용", "관세", "수출규제",
    "최저임금", "코스피", "코스닥", "나스닥", "유가", "국채", "달러", "재정", "예산",
]
CONFIRMATION_WORDS = [
    "공시", "수주", "계약", "공급", "납품", "승인", "허가", "실적", "매출", "영업이익",
    "가이던스", "배당", "자사주", "증자", "품목허가", "임상", "fda",
]
IMAGE_HINT_WORDS = ["[이미지뉴스]", "[이미지OCR]", "[첨부이미지]", "[첨부미디어]", "원문 이미지 확인 필요"]
VAGUE_TITLE_PATTERNS = [
    "블룸버그에 따르면", "로이터에 따르면", "외신에 따르면", "속보", "단독", "긴급", "뉴스", "업데이트",
]
PRICE_REACTION_WORDS = ["급등", "상한가", "폭등", "신고가", "장대양봉"]
ENTRY_ALLOWED_TYPES = {"공시/확정", "이벤트", "실적"}
ENTRY_LABELS = {"관망", "눌림대기", "분할진입 후보"}


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


def _clean_title(title: str) -> str:
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"\s*\(by\s+[@\w가-힣A-Za-z0-9_]+\)?", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*@[\w가-힣]+$", "", title)
    title = re.sub(r"\s*(출처|via|source)[:\s]\S+$", "", title, flags=re.IGNORECASE)
    if title.count("(") > title.count(")"):
        idx = title.rfind("(")
        if idx > 5:
            title = title[:idx]
    return " ".join(title.split()).strip(" -:|·")


def _has_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _cluster_text(cluster) -> str:
    best = cluster.best()
    return f"{best.item.title} {best.item.body}"


def _is_image_news(cluster) -> bool:
    return _has_any(_cluster_text(cluster), IMAGE_HINT_WORDS)


def _has_macro_or_confirmed_content(cluster) -> bool:
    text = _cluster_text(cluster)
    return _has_any(text, MARKET_WIDE_KEEP_WORDS) or _has_any(text, CONFIRMATION_WORDS)


def _body_fallback_title(cluster, limit: int = 95) -> str:
    best = cluster.best()
    body = best.item.body or ""
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\[[^\]]{1,20}\]", "", body)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for line in lines:
        cleaned = _clean_title(line)
        if len(_clean_title_text(cleaned)) >= 8 and not _has_any(cleaned, LOW_VALUE_DISPLAY_WORDS):
            return s.base._short(cleaned, limit)
    cleaned = _clean_title(body)
    return s.base._short(cleaned, limit) if len(_clean_title_text(cleaned)) >= 8 else ""


def _display_title(cluster, limit: int = 95) -> str:
    best = cluster.best()
    raw_title = s.base._short(best.item.title or "", limit)
    clean = _clean_title(raw_title)
    title_key = _clean_title_text(clean)
    vague = len(title_key) < 8 or _has_any(clean, VAGUE_TITLE_PATTERNS)
    if vague:
        fallback = _body_fallback_title(cluster, limit)
        if fallback:
            return fallback
    return clean


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


def _is_display_noise(cluster) -> bool:
    best = cluster.best()
    text = _cluster_text(cluster)
    title_clean = _clean_title_text(_display_title(cluster) or best.item.title)
    symbols = _display_symbols(cluster)
    no_symbols = not symbols
    image_news = _is_image_news(cluster)
    macro_or_confirmed = _has_macro_or_confirmed_content(cluster)

    if _has_any(text, LOW_VALUE_DISPLAY_WORDS) and not (image_news or macro_or_confirmed or symbols):
        return True
    if len(title_clean) < 8 and not (image_news or macro_or_confirmed or symbols):
        return True
    if best.news_type == "가격반응" and no_symbols and not (macro_or_confirmed or image_news):
        return True
    if best.news_type == "테마" and materiality_grade(cluster) in {"B", "C"} and no_symbols and not (macro_or_confirmed or image_news):
        return True
    if no_symbols and best.news_type not in {"거시", "리스크"}:
        if not macro_or_confirmed and not image_news:
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


def _market_line(market_context: dict | None, overview: str) -> str:
    if not market_context:
        return f"시장 데이터 미확인. 보조지표: {overview}"
    parts = []
    for key, label in [
        ("kospi_change_pct", "KOSPI"),
        ("kosdaq_change_pct", "KOSDAQ"),
        ("sp500_change_pct", "S&P500"),
        ("nasdaq_change_pct", "Nasdaq"),
    ]:
        val = market_context.get(key)
        if isinstance(val, (int, float)):
            parts.append(f"{label} {val:+.2f}%")
    sectors = market_context.get("top_sectors_by_volume") or []
    suffix = f" / 거래대금 섹터: {' > '.join(sectors[:3])}" if sectors else ""
    return (" / ".join(parts) if parts else "시장 등락률 미확인") + suffix


def _header_for_kind(kind: str) -> str:
    mapping = {
        "kr_premarket": "📊 [국내주식] 장전 뉴스 브리핑",
        "kr_aftermarket": "📊 [국내주식] 장후 뉴스 브리핑",
        "us_premarket_before": "📊 [미국주식] 프리장전 뉴스 브리핑",
        "us_premarket_after": "📊 [미국주식] 프리장후·정규장 직전 뉴스 브리핑",
        "premarket": "📊 [국내주식] 장전 뉴스 브리핑",
        "aftermarket": "📊 [국내주식] 장후 뉴스 브리핑",
        "intraday": "📊 [국내주식] 장중 뉴스 브리핑",
    }
    return mapping.get(kind, "📊 [주식] 뉴스 브리핑")


def _issue_payload(selected: list) -> list[dict]:
    issues = []
    for idx, cluster in enumerate(selected, 1):
        best = cluster.best()
        issues.append({
            "id": idx,
            "score": materiality_score(cluster),
            "grade": materiality_grade(cluster),
            "type": best.news_type,
            "title": _display_title(cluster, 95),
            "body": best.item.body[:450],
            "sectors": cluster.sectors(),
            "symbols": [{"name": sym.name, "ticker": sym.ticker} for sym in _display_symbols(cluster)],
            "reasons": best.reasons[:5],
            "channel_count": cluster.channel_count(),
            "image_news": _is_image_news(cluster),
        })
    return issues


def _extract_json_object(text: str) -> dict | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start:end + 1])
                return data if isinstance(data, dict) else None
            except Exception:
                return None
    return None


def _audit_report_text(text: str, selected: list) -> tuple[bool, str]:
    if not text.strip():
        return False, "empty"
    lowered = text.lower()
    if any(word in lowered for word in ["비트코인", "이더리움", "코인", "업비트", "바이낸스", "crypto"]):
        return False, "crypto_leak"
    if any(word in text for word in ["목표가", "손절가", "확정 매수", "무조건 매수"]):
        return False, "hard_trading_instruction"
    if len(text) > 2200:
        return False, "too_long"
    if selected and "진입고려" not in text:
        return False, "missing_entry_consideration"
    for label in re.findall(r"진입고려:\s*\[?([^\]\n|]+)", text):
        cleaned = label.strip()
        if cleaned and cleaned not in ENTRY_LABELS:
            return False, f"bad_entry_label:{cleaned}"
    item_count = len(re.findall(r"\n\d+\)", "\n" + text))
    if item_count > len(selected):
        return False, "invented_extra_issue"
    return True, "pass"


def _gemini_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count, market_context):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY 없음"
    if not selected:
        return None, "기준 통과 이슈 0개라 Gemini 호출 생략"
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "header": _header_for_kind(kind),
        "briefing_kind": kind,
        "time_kst": now.strftime("%m/%d %H:%M KST"),
        "hours": hours,
        "market_overview": overview,
        "market_context": market_context,
        "source_count": source_count,
        "quality": {
            "stock_candidate_count": stock_count,
            "excluded_count": blocked,
            "pre_gate_issue_count": pre_gate_count,
            "selected_issue_count": len(selected),
            "rule": rule,
        },
        "issues": _issue_payload(selected[:MAX_DISPLAY_NEWS]),
    }
    prompt = (
        "너는 한국/미국 주식시장 뉴스 데스크이자 단기 트레이더 어시스턴트다.\n\n"
        "[규칙]\n"
        "- 입력 JSON에 있는 이슈만 사용한다. 없는 사실, 없는 종목, 없는 시장데이터를 만들지 않는다.\n"
        "- 코인/가상자산 언급 금지. 목표가/손절가/확정 매수 지시 금지.\n"
        "- 종목명(티커) 형식만 사용한다. URL은 출력하지 않는다.\n"
        "- 급등/상한가/폭등/신고가 뉴스는 진입고려를 반드시 [관망]으로 분류한다.\n"
        "- 공시/수주/계약/실적/승인/허가 뉴스만 [분할진입 후보]로 분류할 수 있다.\n"
        "- 진입고려는 반드시 [관망 | 눌림대기 | 분할진입 후보] 중 하나만 사용한다.\n"
        "- market_context가 null이면 시황 1줄에 시장 데이터 미확인이라고 쓴다.\n\n"
        "[출력 형식]\n"
        "📊 [{시장}] {BRIEFING_KIND별 제목}\n"
        "━━━━━━━━━━━━━━\n"
        "{시간} KST | 최근 {n}시간 | 이슈 {n}개\n"
        "시황 1줄: {지수 방향 + 주요 섹터 흐름. 시장 데이터 없으면 시장 데이터 미확인}\n\n"
        "📌 핵심 이슈\n"
        "1) [{score}/{grade}] {이슈 제목 - 60자 이내}\n"
        "  • 요지: 사실 기반 1문장\n"
        "  • 섹터영향: 1문장\n"
        "  • 진입고려: [관망 | 눌림대기 | 분할진입 후보] 중 1개 + 이유 1문장\n"
        "  • 관련: {종목명(티커)} 또는 직접 언급 없음\n"
        "  • 주의: 최대 리스크 1문장\n\n"
        "⚡ 관심 섹터 순위: {섹터1} > {섹터2} > {섹터3}\n"
        f"검증: Gemini({model}) · 로컬사후감사 · 소스{{n}}개\n\n"
        "반드시 JSON 객체만 반환한다. 키는 report, audit 두 개다. audit은 {pass:boolean, score:number, reason:string}.\n"
        "전체 report는 2100자 이하.\n"
        f"입력 JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.05, "maxOutputTokens": 1100, "responseMimeType": "application/json"},
    }
    try:
        response = requests.post(url, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json=body, timeout=25)
    except Exception as exc:
        return None, f"Gemini 요청 예외: {type(exc).__name__}: {exc}"
    if response.status_code != 200:
        return None, f"Gemini HTTP {response.status_code}: {' '.join(response.text.split())[:180]}"
    try:
        data = response.json()
        raw = "".join(part.get("text", "") for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])).strip()
        parsed = _extract_json_object(raw)
        if not parsed:
            return None, "Gemini 리포트 JSON 파싱 실패"
        audit = parsed.get("audit") or {}
        if audit.get("pass") is False or int(audit.get("score") or 0) < 80:
            return None, f"Gemini 자체감사 실패: {audit}"
        text = str(parsed.get("report") or "").strip()
        ok, reason = _audit_report_text(text, selected)
        if not ok:
            return None, f"로컬사후감사 실패: {reason}"
        if "검증:" not in text:
            text += f"\n검증: Gemini({model}) · 로컬사후감사 · 소스{source_count}개"
        return text[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(text) > MAX_REPORT_CHARS else text, "Gemini 리포트 성공"
    except Exception as exc:
        return None, f"Gemini 리포트 처리 실패: {type(exc).__name__}"


def _entry_consideration(cluster) -> str:
    best = cluster.best()
    text = _cluster_text(cluster)
    if _has_any(text, PRICE_REACTION_WORDS):
        return "[관망] 이미 가격 반응이 포함된 뉴스라 추격 판단은 제외."
    if best.news_type in ENTRY_ALLOWED_TYPES and _has_any(text, CONFIRMATION_WORDS):
        return "[분할진입 후보] 확정성 재료지만 가격·거래대금 확인 후 제한적으로 검토."
    if best.news_type in {"테마", "정보", "가격반응"}:
        return "[관망] 확정 근거가 약하거나 사후성 이슈."
    return "[눌림대기] 이슈 강도는 있으나 선반영 여부 확인 필요."


def _local_insight_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count, market_context, engine: str) -> str:
    display = _drop_noise(selected)[:MAX_DISPLAY_NEWS]
    if not display and os.getenv("SEND_EMPTY_REPORT", "1") == "0":
        return ""
    lines = [
        html.escape(_header_for_kind(kind), quote=False),
        "━━━━━━━━━━━━━━",
        html.escape(f"{now:%m/%d %H:%M KST} | 최근 {hours}시간 | 이슈 {len(display)}개", quote=False),
        html.escape(f"시황 1줄: {_market_line(market_context, overview)}", quote=False),
        "",
    ]
    if not display:
        lines.extend([
            "🔇 이 시간대 주요 이슈 없음",
            html.escape(f"원문 {source_count}건 검토", quote=False),
        ])
    else:
        lines.append("📌 핵심 이슈")
        for idx, cluster in enumerate(display, 1):
            best = cluster.best()
            title = _display_title(cluster, 60)
            symbols = _display_symbols(cluster)
            related = ", ".join(f"{sym.name}({sym.ticker})" for sym in symbols) if symbols else "직접 언급 없음"
            sectors = ", ".join(cluster.sectors()[:3]) or "섹터 불명확"
            lines.append(html.escape(f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}] {title}", quote=False))
            lines.append(html.escape(f"  • 요지: {s.base.TYPE_MEANING.get(best.news_type, '뉴스 흐름 확인용')} · 근거 {', '.join(best.reasons[:3])}", quote=False))
            lines.append(html.escape(f"  • 섹터영향: {sectors} 관련 수급 확인 필요.", quote=False))
            lines.append(html.escape(f"  • 진입고려: {_entry_consideration(cluster)}", quote=False))
            lines.append(html.escape(f"  • 관련: {related}", quote=False))
            lines.append(html.escape(f"  • 주의: {best.item.risk}", quote=False))
            lines.append("")
        lines.append(html.escape(f"⚡ 관심 섹터 순위: {_brief_sector_line(display)}", quote=False))
    lines.append(html.escape(f"검증: {engine} · {rule} · 원문 {source_count}건 → {len(display)}개 선별", quote=False))
    report = "\n".join(lines).strip()
    return report[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    selected, stock_count, blocked, rule, pre_gate_count = s._select_strict(summaries)
    overview = s.base._overview()
    market_context = get_market_context()
    gemini, reason = _gemini_report(
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
        market_context=market_context,
    )
    if gemini:
        return gemini
    local = _local_insight_report(
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
        market_context=market_context,
        engine="로컬인사이트엔진",
    )
    return _append_diag(local, reason)
