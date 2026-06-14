from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
from datetime import datetime, timedelta
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
DISPLAY_HISTORY_PATH = Path(os.getenv("DISPLAYED_NEWS_HISTORY_PATH", "reports/displayed_news_history.json"))
LATEST_REPORT_PATH = Path(os.getenv("LATEST_REPORT_JSON_PATH", "reports/latest_report.json"))
NEWS_REPEAT_SUPPRESS_HOURS = int(os.getenv("NEWS_REPEAT_SUPPRESS_HOURS", "6"))

# False positives produced by generic words or theme terms. Do not put real explicit tickers
# such as SPCX/KORU/MSTR here; if the source writes $TICKER it should be shown.
BAD_DISPLAY_TICKERS = {"IDF", "ESS", "NIM", "GLP", "STRC", "DRAM", "NWS", "NWSA"}
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
MATERIAL_UPDATE_WORDS = ["정정", "추가", "재공시", "확정", "공시", "잠정", "체결", "해지", "승인", "불허", "소송", "제재"]


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


def _signature_text(text: str) -> str:
    return _clean_title_text(text).lower()


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
        common_korean_us = ticker in {"NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "IBM", "AMD", "AVGO", "PLTR", "INTC", "ORCL", "NFLX", "MU", "SMCI", "MSTR", "KORU", "SPCX"} and name_hit
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
    usd = market_context.get("usd_krw")
    if isinstance(usd, (int, float)):
        parts.append(f"USD/KRW {usd:,.1f}")
    sectors = market_context.get("top_sectors_by_volume") or []
    suffix = f" / 거래대금 섹터: {' > '.join(sectors[:3])}" if sectors else ""
    return (" / ".join(parts) if parts else "시장 등락률 미확인") + suffix


def _supply_line(market_context: dict | None) -> str:
    if not market_context:
        return "수급 데이터 미확인"
    bias = str(market_context.get("market_bias") or "시장 판단 미확인")
    flow = str(market_context.get("supply_demand_line") or "투자자별 수급 확인불가")
    return f"{bias} / {flow}"


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


def _parse_message_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Seoul"))
        return dt.astimezone(ZoneInfo("Asia/Seoul"))
    except Exception:
        return None


def _cluster_datetimes(cluster) -> list[datetime]:
    dates: list[datetime] = []
    for news in getattr(cluster, "items", []) or []:
        for value in getattr(news.item, "message_dates", []) or []:
            dt = _parse_message_dt(value)
            if dt:
                dates.append(dt)
    return dates


def _relative_time(dt: datetime, now: datetime) -> str:
    seconds = max(0, int((now - dt).total_seconds()))
    minutes = seconds // 60
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}시간 {minutes % 60}분 전"
    return f"{hours // 24}일 전"


def _age_line(cluster, now: datetime) -> str:
    dts = _cluster_datetimes(cluster)
    if not dts:
        return "시각 미확인"
    latest = max(dts)
    first = min(dts)
    count = sum(getattr(news.item, "repeat_count", 1) for news in getattr(cluster, "items", []) or [])
    if first == latest:
        return f"최신 {_relative_time(latest, now)} / 반복 {count}건"
    return f"최신 {_relative_time(latest, now)} / 최초 {_relative_time(first, now)} / 반복 {count}건"


def _issue_signature(cluster) -> str:
    title = _signature_text(_display_title(cluster, 120))[:120]
    symbols = ",".join(sym.ticker for sym in _display_symbols(cluster))
    sectors = ",".join(cluster.sectors()[:3])
    key = f"{symbols}|{sectors}|{title}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _load_display_history(now: datetime) -> dict[str, str]:
    cutoff = now - timedelta(hours=NEWS_REPEAT_SUPPRESS_HOURS)
    if not DISPLAY_HISTORY_PATH.exists():
        return {}
    try:
        raw = json.loads(DISPLAY_HISTORY_PATH.read_text(encoding="utf-8"))
        items = raw.get("items", {}) if isinstance(raw, dict) else {}
        out: dict[str, str] = {}
        for sig, ts in items.items():
            dt = _parse_message_dt(str(ts))
            if dt and dt >= cutoff:
                out[str(sig)] = str(ts)
        return out
    except Exception:
        return {}


def _save_display_history(history: dict[str, str], displayed: list, now: datetime) -> None:
    try:
        DISPLAY_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        for cluster in displayed:
            history[_issue_signature(cluster)] = now.isoformat()
        cutoff = now - timedelta(hours=NEWS_REPEAT_SUPPRESS_HOURS)
        trimmed = {sig: ts for sig, ts in history.items() if (_parse_message_dt(ts) or now) >= cutoff}
        DISPLAY_HISTORY_PATH.write_text(json.dumps({"updated_at": now.isoformat(), "items": trimmed}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _previous_report_text() -> str:
    if not LATEST_REPORT_PATH.exists():
        return ""
    try:
        data = json.loads(LATEST_REPORT_PATH.read_text(encoding="utf-8"))
        return _signature_text(str(data.get("report") or ""))
    except Exception:
        return ""


def _is_material_update(cluster) -> bool:
    text = _cluster_text(cluster)
    return materiality_score(cluster) >= 90 and _has_any(text, MATERIAL_UPDATE_WORDS)


def _suppress_recent_duplicates(clusters: list, now: datetime) -> tuple[list, int]:
    history = _load_display_history(now)
    previous = _previous_report_text()
    kept: list = []
    suppressed = 0
    for cluster in clusters:
        sig = _issue_signature(cluster)
        title_key = _signature_text(_display_title(cluster, 120))[:120]
        in_previous = bool(title_key and len(title_key) >= 12 and title_key in previous)
        if (sig in history or in_previous) and not _is_material_update(cluster):
            suppressed += 1
            continue
        kept.append(cluster)
    _save_display_history(history, kept, now)
    return kept, suppressed


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
    raw_display = _drop_noise(selected)
    display, suppressed = _suppress_recent_duplicates(raw_display, now)
    display = display[:MAX_DISPLAY_NEWS]
    if not display and os.getenv("SEND_EMPTY_REPORT", "1") == "0":
        return ""
    lines = [
        _header_for_kind(kind),
        "----------------",
        f"{now:%m/%d %H:%M KST} | 최근 {hours}시간 | 신규 이슈 {len(display)}개",
        f"시황 1줄: {_market_line(market_context, overview)}",
        f"수급/시장: {_supply_line(market_context)}",
        "선별방식: 매매전략 없이 뉴스 중요도·신선도·수급 배경만 표시",
        "",
    ]
    if not display:
        lines.extend(["🔇 이 시간대 새 주요 이슈 없음", f"원문 {source_count}건 검토 · 반복/기출 뉴스 {suppressed}건 억제"])
    else:
        lines.append("📌 핵심 이슈")
        for idx, cluster in enumerate(display, 1):
            title = _display_title(cluster, 80)
            symbols = _display_symbols(cluster)
            related = ", ".join(f"{sym.name}({sym.ticker})" for sym in symbols) if symbols else "직접 언급 없음"
            source_url = _source_url(cluster)
            lines.append(f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}] {title}")
            lines.append(f"  • 시각: {_age_line(cluster, now)}")
            if source_url:
                lines.append(f"  • 원문: {source_url}")
            lines.append(f"  • 관련종목: {related}")
            lines.append("")
        lines.append(f"⚡ 관심 섹터 순위: {_brief_sector_line(display)}")
        if suppressed:
            lines.append(f"♻️ 반복/기출 뉴스 억제: {suppressed}건")
    lines.append(f"검증: {engine} · {rule} · 원문 {source_count}건 → 신규 {len(display)}개 선별 · 중복억제 {suppressed}건")
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
