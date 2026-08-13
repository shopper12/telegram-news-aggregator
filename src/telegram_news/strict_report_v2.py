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
HARD_TRADING_WORDS = ["목표가", "손절가", "진입가", "매수 추천", "매도 추천", "무조건 매수", "확정 상승"]


def _append_diag(report: str, reason: str) -> str:
    if not report or os.getenv("DEBUG_QUALITY", "0") != "1":
        return report
    diag = f"\nGemini진단: {reason}"
    return report[: MAX_REPORT_CHARS - len(diag) - 20] + diag


def _clean_title_text(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[#@][\w가-힣_]+", "", text)
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", text).strip()


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
    return _has_any(_cluster_text(cluster), MARKET_WIDE_KEEP_WORDS + CONFIRMATION_WORDS)


def _body_fallback_title(cluster, limit: int = 95) -> str:
    body = cluster.best().item.body or ""
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\[[^\]]{1,20}\]", "", body)
    for line in [line.strip() for line in body.splitlines() if line.strip()]:
        cleaned = _clean_title(line)
        if len(_clean_title_text(cleaned)) >= 8 and not _has_any(cleaned, LOW_VALUE_DISPLAY_WORDS):
            return s.base._short(cleaned, limit)
    cleaned = _clean_title(body)
    return s.base._short(cleaned, limit) if len(_clean_title_text(cleaned)) >= 8 else ""


def _display_title(cluster, limit: int = 95) -> str:
    raw = s.base._short(cluster.best().item.title or "", limit)
    clean = _clean_title(raw)
    if len(_clean_title_text(clean)) < 8 or _has_any(clean, VAGUE_TITLE_PATTERNS):
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
        explicit_us_hit = bool(
            re.search(
                rf"(?:\${re.escape(ticker)}|\({re.escape(ticker)}\)|NASDAQ:{re.escape(ticker)}|NYSE:{re.escape(ticker)}|AMEX:{re.escape(ticker)})\b",
                text,
                re.IGNORECASE,
            )
        )
        common_us = ticker in {
            "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "IBM", "AMD",
            "AVGO", "PLTR", "INTC", "ORCL", "NFLX", "MU", "SMCI", "MSTR", "KORU",
            "EWY", "CRWV", "NBIS", "LITE", "COHR", "CIEN", "VRT", "BE", "IONQ",
            "RGTI", "OKLO", "NNE", "ALB", "RKLB",
        } and name_hit
        if (name_hit or kr_code_hit or explicit_us_hit or common_us) and sym.ticker not in seen:
            seen.add(sym.ticker)
            out.append(sym)
    return out[:6]


def _source_url(cluster) -> str:
    candidates = []
    for news in getattr(cluster, "items", []) or []:
        candidates.extend(getattr(news.item, "source_urls", []) or [])
    for url in candidates:
        value = str(url or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _is_display_noise(cluster) -> bool:
    best = cluster.best()
    text = _cluster_text(cluster)
    symbols = _display_symbols(cluster)
    image_news = _is_image_news(cluster)
    macro_or_confirmed = _has_macro_or_confirmed_content(cluster)
    if _has_any(text, LOW_VALUE_DISPLAY_WORDS) and not (image_news or macro_or_confirmed or symbols):
        return True
    if len(_clean_title_text(_display_title(cluster) or best.item.title)) < 8 and not (image_news or macro_or_confirmed or symbols):
        return True
    if best.news_type == "가격반응" and not symbols and not (macro_or_confirmed or image_news):
        return True
    if best.news_type == "테마" and materiality_grade(cluster) in {"B", "C"} and not symbols and not (macro_or_confirmed or image_news):
        return True
    if not symbols and best.news_type not in {"거시", "리스크"} and not (macro_or_confirmed or image_news):
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
    return " > ".join(sector for sector, _ in counter.most_common(4)) if counter else "섹터 불명확"


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
        value = market_context.get(key)
        if isinstance(value, (int, float)):
            parts.append(f"{label} {value:+.2f}%")
    usd = market_context.get("usd_krw")
    if isinstance(usd, (int, float)):
        parts.append(f"USD/KRW {usd:,.1f}")
    return " / ".join(parts) if parts else "시장 등락률 미확인"


def _supply_line(market_context: dict | None) -> str:
    if not market_context:
        return "수급 데이터 미확인"
    return (
        f"{market_context.get('market_bias') or '시장 판단 미확인'} / "
        f"{market_context.get('supply_demand_line') or '투자자별 수급 확인불가'}"
    )


def _header_for_kind(kind: str) -> str:
    mapping = {
        "us_close": "🇺🇸 미국증시 마감 → 🇰🇷 한국장 프리뷰",
        "kr_premarket": "📊 국내증시 장전 브리핑",
        "premarket": "📊 국내증시 장전 브리핑",
        "kr_aftermarket": "📊 국내증시 마감 브리핑",
        "aftermarket": "📊 국내증시 마감 브리핑",
        "intraday": "📊 국내증시 장중 뉴스",
        "us_premarket_before": "🇺🇸 미국증시 장전 브리핑",
        "us_premarket_after": "🇺🇸 미국증시 장전 브리핑",
    }
    return mapping.get(kind, "📊 주식시장 뉴스")


def _parse_message_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Seoul"))
        return dt.astimezone(ZoneInfo("Asia/Seoul"))
    except Exception:
        return None


def _cluster_datetimes(cluster) -> list[datetime]:
    dates = []
    for news in getattr(cluster, "items", []) or []:
        for value in getattr(news.item, "message_dates", []) or []:
            dt = _parse_message_dt(value)
            if dt:
                dates.append(dt)
    return dates


def _relative_time(dt: datetime, now: datetime) -> str:
    minutes = max(0, int((now - dt).total_seconds())) // 60
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 1440:
        return f"{minutes // 60}시간 {minutes % 60}분 전"
    return f"{minutes // 1440}일 전"


def _age_line(cluster, now: datetime) -> str:
    dts = _cluster_datetimes(cluster)
    if not dts:
        return "시각 미확인"
    latest, first = max(dts), min(dts)
    count = sum(getattr(news.item, "repeat_count", 1) for news in getattr(cluster, "items", []) or [])
    if first == latest:
        return f"최신 {_relative_time(latest, now)} / 반복 {count}건"
    return f"최신 {_relative_time(latest, now)} / 최초 {_relative_time(first, now)} / 반복 {count}건"


def _issue_signature(cluster) -> str:
    key = "|".join(
        [
            ",".join(sym.ticker for sym in _display_symbols(cluster)),
            ",".join(cluster.sectors()[:3]),
            _signature_text(_display_title(cluster, 120))[:120],
        ]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _load_display_history(now: datetime) -> dict[str, str]:
    cutoff = now - timedelta(hours=NEWS_REPEAT_SUPPRESS_HOURS)
    if not DISPLAY_HISTORY_PATH.exists():
        return {}
    try:
        raw = json.loads(DISPLAY_HISTORY_PATH.read_text(encoding="utf-8"))
        items = raw.get("items", {}) if isinstance(raw, dict) else {}
        return {
            str(sig): str(ts)
            for sig, ts in items.items()
            if (_parse_message_dt(str(ts)) or datetime.min.replace(tzinfo=ZoneInfo("Asia/Seoul"))) >= cutoff
        }
    except Exception:
        return {}


def _save_display_history(history: dict[str, str], displayed: list, now: datetime) -> None:
    try:
        DISPLAY_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        for cluster in displayed:
            history[_issue_signature(cluster)] = now.isoformat()
        cutoff = now - timedelta(hours=NEWS_REPEAT_SUPPRESS_HOURS)
        trimmed = {
            sig: ts
            for sig, ts in history.items()
            if (_parse_message_dt(ts) or now) >= cutoff
        }
        DISPLAY_HISTORY_PATH.write_text(
            json.dumps({"updated_at": now.isoformat(), "items": trimmed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return


def _previous_report_text() -> str:
    if not LATEST_REPORT_PATH.exists():
        return ""
    try:
        data = json.loads(LATEST_REPORT_PATH.read_text(encoding="utf-8"))
        return str(data.get("report") or "")
    except Exception:
        return ""


def _is_material_update(cluster) -> bool:
    return materiality_score(cluster) >= 90 and _has_any(_cluster_text(cluster), MATERIAL_UPDATE_WORDS)


def _suppress_recent_duplicates(clusters: list, now: datetime) -> tuple[list, int]:
    history = _load_display_history(now)
    previous = _signature_text(_previous_report_text())
    kept, suppressed = [], 0
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


def _entry_consideration(cluster) -> str:
    if cluster.best().news_type == "가격반응":
        return "[관망] 이미 가격반응이 나온 뉴스"
    if materiality_score(cluster) >= 85:
        return "[체크] 가격·거래대금 확인 필요"
    return "[관망] 뉴스 단독 판단 금지"


def _audit_report_text(text: str, selected: list) -> tuple[bool, str]:
    if not text.strip():
        return False, "empty"
    lowered = text.lower()
    if any(word in lowered for word in ["비트코인", "이더리움", "코인", "업비트", "바이낸스", "crypto"]):
        return False, "crypto_leak"
    if any(word in text for word in HARD_TRADING_WORDS):
        return False, "hard_trading_instruction"
    return True, "pass"


def _cluster_payload(clusters: list, now: datetime) -> list[dict]:
    out = []
    for idx, cluster in enumerate(clusters[:20], 1):
        best = cluster.best()
        out.append(
            {
                "rank": idx,
                "importance_score": materiality_score(cluster),
                "grade": materiality_grade(cluster),
                "news_type": best.news_type,
                "title": _display_title(cluster, 120),
                "body": s.base._short(best.item.body or "", 420),
                "sectors": cluster.sectors()[:5],
                "symbols": [
                    {"name": str(sym.name or ""), "ticker": sym.ticker}
                    for sym in _display_symbols(cluster)
                ],
                "age": _age_line(cluster, now),
                "source_url": _source_url(cluster),
            }
        )
    return out


def _extract_json_object(text: str) -> dict | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except Exception:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(cleaned[start : end + 1])
                return value if isinstance(value, dict) else None
            except Exception:
                return None
    return None


def _dashboard_prompt(payload: dict) -> str:
    return (
        "너는 한국 투자자를 위한 글로벌 크로스애셋 시장 데스크, 뉴스 편집장, 데이터 검증 담당자다.\n"
        "입력 JSON에 있는 사실과 숫자만 사용한다. 숫자·등락률·실적·가이던스·일정을 절대 추정하지 않는다.\n"
        "값이 없으면 '확인불가'라고 쓰고, 사실과 해석을 구분한다. 급등 자체를 상승 원인으로 쓰지 않는다.\n"
        "뉴스 제목을 나열하지 말고 원인→시장반응→섹터 확산→한국장 전이→리스크 순으로 재구성한다.\n"
        "global_market_quotes의 source/timestamp를 신뢰도 기준으로 삼고 정규장 수치와 다른 시점 수치를 섞지 않는다.\n"
        "sector_baskets는 당일 미국 업종 확산도 확인용이다. 한 종목만 움직이면 개별주, 복수 종목 동반이면 섹터 확산이라고 쓴다.\n"
        "한국장 연결은 EWY/KORU, SOX, USD/KRW, 한국 수급, 관련 미국 업종 중 실제 입력 근거가 있는 것만 사용한다.\n"
        "CPI/PPI/FOMC/고용 등 일정은 news_issues 안에 실제 시각이나 예정 사실이 있을 때만 적는다.\n"
        "투자 지시, 목표가, 손절가, 확정 상승 표현은 금지한다. 가상자산 내용도 쓰지 않는다.\n"
        "반드시 JSON 객체 하나만 반환한다. 키는 report, audit이다.\n"
        "audit={\"pass\":true|false,\"score\":0~100,\"reason\":\"...\"} 형식이다. 사실 검증 자신도가 85 미만이면 pass=false.\n\n"
        "report 형식:\n"
        "🇺🇸 MM/DD 미국증시 마감 → 🇰🇷 한국장 프리뷰\n"
        "🔥 오늘의 한줄: [핵심 촉매] → [미국시장 반응] → [한국장 핵심 영향]\n\n"
        "📊 주요 지수\n"
        "- 다우 / S&P500 / 나스닥 / 러셀2000 / SOX: 값과 등락률\n\n"
        "🌡️ 주요 시장 지표\n"
        "- DXY / 미국10년물 / VIX / WTI / USDKRW\n"
        "- 시장 레짐: RISK-ON|NEUTRAL|RISK-OFF\n\n"
        "🧭 시장이 움직인 핵심 원인\n"
        "1) 사실 / 시장반응 / 의미\n"
        "2) 필요시 추가\n\n"
        "🚀 주도 섹터\n"
        "- AI인프라, 반도체·메모리, 광통신·네트워크, 전력·데이터센터, 양자, 원자력, 배터리·리튬, 우주 중 실제 움직임이 확인된 것만\n"
        "- 대표종목 등락률과 촉매, 확산도\n\n"
        "🇰🇷 한국장 영향\n"
        "- EWY / KORU / USDKRW / 외국인·기관 수급\n"
        "- 유리한 섹터 / 부담 섹터\n"
        "- 판정: 강세 우위|선별 강세|중립|방어 우위 + 이유\n\n"
        "⚠️ 오늘의 리스크\n"
        "- 갭상승 후 차익실현, 선반영, 금리·달러 반전, 이벤트 변동성 등 입력 근거가 있는 것\n\n"
        "🗓 주요 일정\n"
        "- 입력 뉴스에 확인된 일정만 한국시간으로 표시. 없으면 '확인 가능한 일정 데이터 없음'\n\n"
        "✅ 3줄 요약\n"
        "1. 거시환경과 레짐\n2. 가장 강한 섹터와 이유\n3. 한국장 핵심 체크포인트\n\n"
        "Telegram용이므로 문장은 짧게, 숫자는 정확하게, 전체 7000자 이하.\n\n"
        f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _gemini_report(**kwargs):
    kind = str(kwargs.get("kind") or "")
    if kind != "us_close":
        return None, "gemini_dashboard_only_for_us_close"

    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None, "gemini_api_key_missing"

    market_context = kwargs.get("market_context")
    if not market_context:
        return None, "market_context_missing"

    now = kwargs["now"]
    display = _drop_noise(kwargs.get("selected") or [])[:20]
    payload = {
        "briefing_kind": kind,
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M KST"),
        "lookback_hours": kwargs.get("hours"),
        "market_context": market_context,
        "news_issues": _cluster_payload(display, now),
        "previous_report": _previous_report_text()[:2500],
        "quality": {
            "source_count": kwargs.get("source_count"),
            "stock_candidate_count": kwargs.get("stock_count"),
            "excluded_count": kwargs.get("blocked"),
            "pre_gate_count": kwargs.get("pre_gate_count"),
        },
    }

    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"parts": [{"text": _dashboard_prompt(payload)}]}],
        "generationConfig": {
            "temperature": 0.08,
            "maxOutputTokens": 3500,
            "responseMimeType": "application/json",
        },
    }
    try:
        response = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=40,
        )
        response.raise_for_status()
        data = response.json()
        raw = "".join(
            part.get("text", "")
            for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        ).strip()
        parsed = _extract_json_object(raw)
        if not parsed:
            return None, "gemini_json_parse_failed"
        audit = parsed.get("audit") or {}
        if audit.get("pass") is False or int(audit.get("score") or 0) < 85:
            return None, f"gemini_self_audit_failed:{audit.get('reason') or 'unknown'}"
        report = str(parsed.get("report") or "").strip()
        ok, reason = _audit_report_text(report, display)
        if not ok:
            return None, reason
        if "미국증시" not in report or "한국장" not in report:
            return None, "dashboard_sections_missing"
        if len(report) > MAX_REPORT_CHARS:
            report = report[: MAX_REPORT_CHARS - 20] + "\n… 이하 생략"
        return report, f"gemini_dashboard:{model}:audit={audit.get('score')}"
    except Exception as exc:
        return None, f"gemini_request_failed:{type(exc).__name__}"


def _fmt_quote(item: dict) -> str:
    label = str(item.get("label") or item.get("ticker") or "?")
    price = item.get("price")
    change = item.get("change_pct")
    if not isinstance(price, (int, float)):
        return f"{label} 확인불가"
    price_text = f"{price:,.2f}" if abs(price) < 1000 else f"{price:,.0f}"
    change_text = f" {change:+.2f}%" if isinstance(change, (int, float)) else ""
    return f"{label} {price_text}{change_text}"


def _local_us_close_report(*, now, hours, selected, market_context, source_count, rule) -> str:
    global_quotes = (market_context or {}).get("global_market_quotes") or []
    proxies = (market_context or {}).get("korea_proxies") or []
    baskets = (market_context or {}).get("sector_baskets") or {}
    regime = (market_context or {}).get("risk_regime") or {}
    lines = [
        f"🇺🇸 {now:%m/%d} 미국증시 마감 → 🇰🇷 한국장 프리뷰",
        "━━━━━━━━━━━━━━",
        f"{now:%m/%d %H:%M KST} | 최근 {hours}시간",
        f"🔥 오늘의 한줄: Gemini 상세 분석 실패 시 로컬 검증 데이터로 표시 · 시장 레짐 {regime.get('regime', '확인불가')}",
        "",
        "📊 주요 지수",
    ]
    for label in ["DOW", "S&P500", "NASDAQ", "RUSSELL2000", "SOX"]:
        item = next((q for q in global_quotes if q.get("label") == label), {"label": label})
        lines.append(f"- {_fmt_quote(item)}")
    lines.extend(["", "🌡️ 주요 시장 지표"])
    for label in ["DXY", "US10Y", "VIX", "WTI"]:
        item = next((q for q in global_quotes if q.get("label") == label), {"label": label})
        lines.append(f"- {_fmt_quote(item)}")
    usd = (market_context or {}).get("usd_krw")
    lines.append(f"- USD/KRW {usd:,.1f}" if isinstance(usd, (int, float)) else "- USD/KRW 확인불가")
    lines.append(f"- 시장 레짐: {regime.get('regime', '확인불가')}")
    lines.extend(["", "🚀 주도 섹터"])
    shown = 0
    for sector, members in baskets.items():
        valid = [m for m in members if isinstance(m.get("change_pct"), (int, float))]
        if not valid:
            continue
        shown += 1
        lines.append(f"- {sector}: " + ", ".join(_fmt_quote(m) for m in valid[:3]))
    if not shown:
        lines.append("- 섹터 가격 데이터 확인불가")
    lines.extend(["", "🇰🇷 한국장 영향"])
    lines.append("- " + " / ".join(_fmt_quote(item) for item in proxies) if proxies else "- EWY/KORU 확인불가")
    lines.append(f"- {_supply_line(market_context)}")
    lines.append(f"- 관심 섹터: {_brief_sector_line(selected)}")
    lines.extend(["", "📌 핵심 뉴스"])
    display = _drop_noise(selected)[:10]
    if display:
        for idx, cluster in enumerate(display, 1):
            lines.append(f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}] {_display_title(cluster, 90)}")
    else:
        lines.append("- 중요도 게이트 통과 뉴스 없음")
    lines.extend([
        "",
        "⚠️ 오늘의 리스크",
        "- 상세 촉매 인과관계는 Gemini 분석 실패로 자동 생성하지 않음. 숫자와 뉴스 사실만 표시.",
        "",
        "🗓 주요 일정",
        "- 확인 가능한 일정 데이터는 수집 뉴스에 있는 경우에만 핵심 뉴스에서 확인.",
        "",
        "✅ 3줄 요약",
        f"1. 시장 레짐: {regime.get('regime', '확인불가')}",
        f"2. 미국 섹터 확산: {'확인' if shown else '확인불가'}",
        f"3. 한국장: {(market_context or {}).get('market_bias') or '판단 데이터 부족'}",
        "",
        f"검증: 로컬대시보드 · {rule} · 원문 {source_count}건",
    ])
    report = "\n".join(lines)
    return report[: MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report


def _local_insight_report(*, now, kind, hours, selected, stock_count, blocked, rule, overview, source_count, pre_gate_count, market_context, engine: str) -> str:
    if kind == "us_close":
        return _local_us_close_report(
            now=now,
            hours=hours,
            selected=selected,
            market_context=market_context,
            source_count=source_count,
            rule=rule,
        )

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
            lines.append(f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}] {title}")
            lines.append(f"  • 시각: {_age_line(cluster, now)}")
            if _source_url(cluster):
                lines.append(f"  • 원문: {_source_url(cluster)}")
            lines.append(f"  • 관련종목: {related}")
            lines.append("")
        lines.append(f"⚡ 관심 섹터 순위: {_brief_sector_line(display)}")
        if suppressed:
            lines.append(f"♻️ 반복/기출 뉴스 억제: {suppressed}건")
    lines.append(
        f"검증: {engine} · {rule} · 원문 {source_count}건 → 신규 {len(display)}개 선별 · 중복억제 {suppressed}건"
    )
    report = "\n".join(lines).strip()
    return report[: MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    if kind == "us_close":
        from .market_dashboard_report import build_us_close_dashboard
        return build_us_close_dashboard(summaries, hours=hours, timezone_name=timezone_name)

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
