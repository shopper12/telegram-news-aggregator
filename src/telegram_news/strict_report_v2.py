from __future__ import annotations

from collections import Counter
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

MAX_REPORT_CHARS = int(os.getenv("MAX_REPORT_CHARS", "12000"))
MAX_DISPLAY_NEWS = int(os.getenv("MAX_DISPLAY_NEWS", "999"))
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

BAD_DISPLAY_TICKERS = {"IDF", "ESS", "NIM", "GLP", "MSTR", "STRC", "DRAM", "KORU", "SPCX"}
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
VAGUE_TITLE_PATTERNS = ["블룸버그에 따르면", "로이터에 따르면", "외신에 따르면", "속보", "단독", "긴급", "뉴스", "업데이트"]


def _append_diag(report: str, reason: str) -> str:
    if not report or os.getenv("DEBUG_QUALITY", "0") != "1":
        return report
    diag = f"\nGemini진단: {reason}"
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
    return out[:5]


def _source_url(cluster) -> str:
    candidates = []
    try:
        candidates.extend(getattr(cluster.best().item, "source_urls", []) or [])
    except Exception:
        pass
    for news in getattr(cluster, "items", []) or []:
        try:
            candidates.extend(getattr(news.item, "source_urls", []) or [])
        except Exception:
            continue
    for url in candidates:
        text = str(url or "").strip()
        if text.startswith("http://") or text.startswith("https://"):
            return text
    return ""


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
    for key, label in [("kospi_change_pct", "KOSPI"), ("kosdaq_change_pct", "KOSDAQ"), ("sp500_change_pct", "S&P500"), ("nasdaq_change_pct", "Nasdaq")]:
        val = market_context.get(key)
        if isinstance(val, (int, float)):
            parts.append(f"{label} {val:+.2f}%")
    sectors = market_context.get("top_sectors_by_volume") or []
    suffix = f" / 거래대금 섹터: {' > '.join(sectors[:3])}" if sectors else ""
    return (" / ".join(parts) if parts else "시장 등락률 미확인") + suffix


def _header_for_kind(kind: str) -> str:
    mapping = {
        "kr_premarket": "📊 [국내주식] 최근 1시간 뉴스",
        "kr_aftermarket": "📊 [국내주식] 최근 1시간 뉴스",
        "us_premarket_before": "📊 [미국주식] 최근 1시간 뉴스",
        "us_premarket_after": "📊 [미국주식] 최근 1시간 뉴스",
        "premarket": "📊 [국내주식] 최근 1시간 뉴스",
        "aftermarket": "📊 [국내주식] 최근 1시간 뉴스",
        "intraday": "📊 [국내주식] 최근 1시간 뉴스",
    }
    return mapping.get(kind, "📊 [주식] 최근 1시간 뉴스")


def _audit_report_text(text: str, selected: list) -> tuple[bool, str]:
    if not text.strip():
        return False, "empty"
    lowered = text.lower()
    if any(word in lowered for word in ["비트코인", "이더리움", "코인", "업비트", "바이낸스", "crypto"]):
        return False, "crypto_leak"
    return True, "pass"


def _gemini_report(**kwargs):
    return None, "simplified_local_report_for_kakao"


def _local_insight_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count, market_context, engine: str) -> str:
    display = _drop_noise(selected)[:MAX_DISPLAY_NEWS]
    if not display and os.getenv("SEND_EMPTY_REPORT", "1") == "0":
        return ""
    lines = [_header_for_kind(kind), "----------------", f"{now:%m/%d %H:%M KST} | 최근 {hours}시간 | 이슈 {len(display)}개", f"시황 1줄: {_market_line(market_context, overview)}", ""]
    if not display:
        lines.extend(["🔇 이 시간대 주요 이슈 없음", f"원문 {source_count}건 검토"])
    else:
        lines.append("📌 핵심 이슈")
        for idx, cluster in enumerate(display, 1):
            title = _display_title(cluster, 80)
            symbols = _display_symbols(cluster)
            related = ", ".join(f"{sym.name}({sym.ticker})" for sym in symbols) if symbols else "직접 언급 없음"
            source_url = _source_url(cluster)
            lines.append(f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}] {title}")
            if source_url:
                lines.append(f"  • 원문: {source_url}")
            lines.append(f"  • 관련종목: {related}")
            lines.append("")
        lines.append(f"⚡ 관심 섹터 순위: {_brief_sector_line(display)}")
    lines.append(f"검증: {engine} · {rule} · 원문 {source_count}건 → {len(display)}개 선별")
    report = "\n".join(lines).strip()
    return report[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    selected, stock_count, blocked, rule, pre_gate_count = s._select_strict(summaries)
    overview = s.base._overview()
    market_context = get_market_context()
    gemini, reason = _gemini_report(now=now, kind=kind, hours=hours, selected=selected, stock_count=stock_count, blocked=blocked, rule=rule, overview=overview, source_count=len(summaries), pre_gate_count=pre_gate_count, market_context=market_context)
    if gemini:
        return gemini
    local = _local_insight_report(now=now, kind=kind, hours=hours, selected=selected, stock_count=stock_count, blocked=blocked, rule=rule, overview=overview, source_count=len(summaries), pre_gate_count=pre_gate_count, market_context=market_context, engine="로컬인사이트엔진")
    return _append_diag(local, reason)
