from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os
import re
import sys
from typing import Any
from zoneinfo import ZoneInfo

from . import strict_report_v2 as base_report


KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
MAX_NOTE_SECTORS = int(os.getenv("MARKET_NOTE_MAX_SECTORS", "3"))
MAX_NOTE_CHARS = int(os.getenv("MAX_REPORT_CHARS", "12000"))
MESSENGER_NOTE_MAX_CHARS = int(os.getenv("MESSENGER_NOTE_MAX_CHARS", "8000"))
NOTE_ASSETS = {
    "^DJI": "다우",
    "^GSPC": "S&P500",
    "^IXIC": "나스닥",
    "^RUT": "러셀2000",
    "^SOX": "필라델피아 반도체",
    "^TYX": "미30년물",
    "CL=F": "WTI",
}
GENERIC_SECTORS = {"거시/정책", "거시", "리스크", "정보", "가격반응", "테마"}
_INSTALLED = False
_DISPATCH_HOOKED = False


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _compact(value: Any, limit: int = 120) -> str:
    text = str(value or "")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\(출처:[^)]+\)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -•·")
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _clean_news_text(value: Any, limit: int = 120) -> str:
    text = str(value or "")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"^[\s🔔📈📝🚨⚡📌]+", "", text)
    text = re.sub(r"(?<!\w)#[0-9A-Za-z가-힣_]+", " ", text)
    text = re.sub(r"\s+(?:출처|source)\s*:\s*[^|\n]+(?:\s*\|\s*시각\s*:.*)?$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -•·")
    return _compact(text, limit)


def _sentences(value: Any, limit: int = 3) -> list[str]:
    text = _compact(value, 520)
    if not text:
        return []
    pieces = [
        _compact(piece, 150)
        for piece in re.split(r"(?<=[.!?。！？])\s+|\n+", text)
        if _compact(piece, 150)
    ]
    if len(pieces) <= 1 and len(text) > 160:
        pieces = [_compact(text[index:index + 130], 130) for index in range(0, min(len(text), 390), 130)]
    return pieces[:limit]


def _arrow(value: float | None) -> str:
    if value is None:
        return "· 확인불가"
    return f"▲ {value:+.2f}%" if value > 0 else f"▼ {value:+.2f}%" if value < 0 else "― +0.00%"


def _asset(snapshot: dict[str, Any] | None, ticker: str) -> dict[str, Any]:
    return dict((((snapshot or {}).get("assets") or {}).get(ticker) or {}))


def _asset_change(snapshot: dict[str, Any] | None, ticker: str) -> float | None:
    item = _asset(snapshot, ticker)
    if "validated_change_pct" in item:
        return _safe_float(item.get("validated_change_pct"))
    return _safe_float(item.get("change_pct"))


def _asset_price(snapshot: dict[str, Any] | None, ticker: str) -> float | None:
    return _safe_float(_asset(snapshot, ticker).get("price"))


def _ensure_note_assets(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(snapshot or {})
    assets = dict(result.get("assets") or {})
    missing = [ticker for ticker in NOTE_ASSETS if _safe_float((assets.get(ticker) or {}).get("price")) is None]
    if missing:
        try:
            from .global_market_tracker import fetch_asset_snapshot

            with ThreadPoolExecutor(max_workers=min(5, len(missing)), thread_name_prefix="market-note") as pool:
                futures = {pool.submit(fetch_asset_snapshot, ticker): ticker for ticker in missing}
                for future in as_completed(futures):
                    item = future.result()
                    if isinstance(item, dict) and item.get("ticker"):
                        assets[str(item["ticker"])] = item
        except Exception as exc:
            print(f"[market-note] note asset fetch failed: {type(exc).__name__}: {exc}")
    result["assets"] = assets
    return result


def _ny_market_phase(now: datetime) -> str:
    ny = now.astimezone(NY)
    if ny.weekday() >= 5:
        return "closed"
    minute = ny.hour * 60 + ny.minute
    if minute < 9 * 60 + 30:
        return "premarket"
    if minute < 16 * 60:
        return "regular"
    return "afterhours"


def _note_name(kind: str, now: datetime) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized == "us_close":
        return "미 증시 마감 → 한국장 프리뷰"
    if normalized == "strategy_morning":
        return "미 증시 클로징 노트"
    if normalized == "strategy_evening":
        return "미 증시 오프닝 전략"
    if normalized in {"us_premarket_before", "us_premarket_after"}:
        return "미국 장전 점검"
    if normalized in {"kr_premarket", "premarket"}:
        return "한국 증시 장전 노트"
    if normalized == "intraday":
        return "한국 증시 장중 노트"
    if normalized in {"kr_aftermarket", "aftermarket"}:
        return "한국 증시 클로징 노트"
    if normalized == "overnight":
        return "글로벌 야간 시황 노트"

    minute = now.hour * 60 + now.minute
    ny_phase = _ny_market_phase(now)
    if minute < 9 * 60:
        return "미 증시 마감후 · 한국장 장전" if ny_phase == "afterhours" else "글로벌 장전 브리핑"
    if minute < 15 * 60 + 30:
        return "한국 증시 장중 노트"
    if ny_phase == "premarket":
        return "한국 증시 마감 · 미국 최근 확정세션"
    if ny_phase == "regular":
        return "미 증시 장중 노트"
    if ny_phase == "afterhours":
        return "미 증시 마감후 노트"
    return "한국 증시 마감 · 글로벌 점검"


def _is_us_note(note_name: str) -> bool:
    return note_name.startswith("미 증시")


def _canonical_market_context(
    market_context: dict[str, Any] | None,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    context = dict(market_context or {})
    sp500 = _asset_change(snapshot, "^GSPC")
    nasdaq = _asset_change(snapshot, "^IXIC")
    if sp500 is not None:
        context["sp500_change_pct"] = sp500
    if nasdaq is not None:
        context["nasdaq_change_pct"] = nasdaq
    return context


def _selected_clusters(summaries: list[Any]) -> list[Any]:
    try:
        selected, *_ = base_report.s._select_strict(summaries)
        return list(base_report._drop_noise(selected))
    except Exception:
        return []


def _score_grade(cluster: Any) -> tuple[int, str]:
    try:
        return int(base_report.materiality_score(cluster)), str(base_report.materiality_grade(cluster))
    except Exception:
        score = int(getattr(cluster, "materiality_score_override", 50) or 50)
        grade = str(getattr(cluster, "materiality_grade_override", "C") or "C")
        return score, grade


def _symbols(cluster: Any) -> list[Any]:
    try:
        return list(base_report._display_symbols(cluster))
    except Exception:
        try:
            return list(cluster.symbols())
        except Exception:
            return []


def _source_url(cluster: Any) -> str:
    try:
        return str(base_report._source_url(cluster) or "")
    except Exception:
        return ""


def _cluster_payload(cluster: Any) -> dict[str, Any]:
    best = cluster.best()
    item = best.item
    score, grade = _score_grade(cluster)
    sectors = []
    try:
        sectors = [str(value) for value in cluster.sectors() if str(value).strip()]
    except Exception:
        sectors = [str(value) for value in getattr(item, "sectors", []) if str(value).strip()]
    return {
        "cluster": cluster,
        "title": _clean_news_text(getattr(item, "title", ""), 88),
        "body": _clean_news_text(getattr(item, "body", ""), 500),
        "news_type": str(getattr(best, "news_type", "") or ""),
        "score": score,
        "grade": grade,
        "sectors": sectors,
        "symbols": _symbols(cluster),
        "url": _source_url(cluster),
    }


def _cluster_repeat_support(cluster: Any) -> bool:
    try:
        if int(cluster.channel_count()) >= 2:
            return True
    except Exception:
        pass
    try:
        return len(getattr(cluster, "items", []) or []) >= 2
    except Exception:
        return False


def _sector_price_confirmed(payload: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    sectors = " ".join(str(value or "").lower() for value in payload.get("sectors") or [])
    checks: list[str] = []
    if any(word in sectors for word in ("반도체", "메모리", "ai인프라", "ai 인프라")):
        checks.extend(["^SOX", "SOXX", "^IXIC", "QQQ"])
    elif any(word in sectors for word in ("빅테크", "소프트웨어", "기술주", "ai")):
        checks.extend(["^IXIC", "QQQ"])

    return any(
        (value := _asset_change(snapshot, ticker)) is not None and abs(value) >= 0.75
        for ticker in dict.fromkeys(checks)
    )


def _routine_payload_allowed(
    payload: dict[str, Any],
    *,
    note_name: str,
    snapshot: dict[str, Any],
) -> bool:
    """Routine notes show only evidence that is both credible and decision-relevant."""
    grade = str(payload.get("grade") or "D").upper()
    score = int(payload.get("score") or 0)
    news_type = str(payload.get("news_type") or "")
    if grade == "D":
        return False
    if grade == "C" and not (score >= 75 and _cluster_repeat_support(payload.get("cluster"))):
        return False
    if grade in {"A", "B"} and score < 68:
        return False

    if news_type in {"거시", "리스크"}:
        return True
    if _symbol_text(payload.get("symbols") or [], korean_only=True):
        return True
    return _sector_price_confirmed(payload, snapshot)


def _routine_payload_rank(payload: dict[str, Any], note_name: str) -> tuple[int, int, int]:
    grade = str(payload.get("grade") or "D").upper()
    source_bonus = {"A": 30, "B": 18, "C": 6}.get(grade, -100)
    news_type = str(payload.get("news_type") or "")
    market_wide = 35 if news_type in {"거시", "리스크"} else 0
    korea_direct = 0
    if note_name.startswith("한국 증시"):
        korea_direct = 28 if _symbol_text(payload.get("symbols") or [], korean_only=True) else 0
    symbol_penalty = -12 if not payload.get("symbols") and news_type not in {"거시", "리스크"} else 0
    return (int(payload.get("score") or 0) + source_bonus + market_wide + korea_direct + symbol_penalty, source_bonus, int(payload.get("score") or 0))


def _market_data_warning(snapshot: dict[str, Any]) -> str:
    warnings = [str(value).strip() for value in snapshot.get("validation_warnings") or [] if str(value).strip()]
    return " / ".join(warnings[:2])


def _source_confidence_text(payload: dict[str, Any]) -> str:
    grade = str(payload.get("grade") or "D").upper()
    return {"A": "복수 원문 확인", "B": "원문 확인", "C": "복수 채널 반복"}.get(grade, "검증 부족")


def _why_it_matters(payload: dict[str, Any]) -> str:
    news_type = str(payload.get("news_type") or "")
    kr = _symbol_text(payload.get("symbols") or [], korean_only=True)
    symbols = _symbol_text(payload.get("symbols") or [])
    sectors = [str(value) for value in (payload.get("sectors") or []) if str(value).strip() and str(value) not in GENERIC_SECTORS]
    if news_type in {"거시", "리스크"}:
        return "금리·환율·지수 전반에 번질 수 있는 시장 전체 변수"
    if kr:
        return f"한국 상장사 직접 재료: {kr}"
    if sectors:
        return f"섹터 영향 확인 필요: {' · '.join(sectors[:2])}"
    if symbols:
        return f"직접 관련 종목: {symbols}"
    return "시장 전체 영향은 아직 제한적"


def _asset_session_date(snapshot: dict[str, Any], ticker: str) -> str:
    return str(_asset(snapshot, ticker).get("session_date") or "").strip()


def _same_session_change(snapshot: dict[str, Any], ticker: str, anchor_date: str) -> float | None:
    if not anchor_date or _asset_session_date(snapshot, ticker) != anchor_date:
        return None
    return _asset_change(snapshot, ticker)


def _routine_market_read(snapshot: dict[str, Any]) -> str:
    session = _asset_session_date(snapshot, "^GSPC")
    sp500 = _same_session_change(snapshot, "^GSPC", session)
    nasdaq = _same_session_change(snapshot, "^IXIC", session)
    sox = _same_session_change(snapshot, "^SOX", session)
    russell = _same_session_change(snapshot, "^RUT", session)
    values = [value for value in (sp500, nasdaq, sox, russell) if value is not None]
    prefix = f"미국 {session} 확정세션" if session else "미국 최근 확정세션"
    if len(values) < 2:
        return f"{prefix}: 방향 판정 보류"
    if sp500 is not None and nasdaq is not None and sox is not None and sp500 > 0 and nasdaq > 0 and sox > 0:
        if sox - sp500 >= 1.0:
            return f"{prefix}: 지수 상승 + 반도체 상대강세"
        return f"{prefix}: S&P500·Nasdaq·반도체 동반 상승"
    if sp500 is not None and nasdaq is not None and sp500 < 0 and nasdaq < 0:
        return f"{prefix}: 대형지수 동반 약세"
    positives = sum(value > 0 for value in values)
    negatives = sum(value < 0 for value in values)
    if positives and negatives:
        return f"{prefix}: 지수 내부 혼조"
    return f"{prefix}: 방향성 제한"


def _routine_watch_lines(snapshot: dict[str, Any]) -> list[str]:
    session = _asset_session_date(snapshot, "^GSPC")
    sp500 = _same_session_change(snapshot, "^GSPC", session)
    nasdaq = _same_session_change(snapshot, "^IXIC", session)
    sox = _same_session_change(snapshot, "^SOX", session)
    lines: list[str] = []
    if sox is not None and sp500 is not None and abs(sox - sp500) >= 0.75:
        lines.append(f"반도체 상대강도: SOX가 S&P500 대비 {sox - sp500:+.2f}%p — 다음 확정세션에도 유지되는지")
    if nasdaq is not None and sp500 is not None and abs(nasdaq - sp500) >= 0.40:
        lines.append(f"기술주 상대강도: Nasdaq이 S&P500 대비 {nasdaq - sp500:+.2f}%p — 확산/축소 여부")
    vix = _asset_price(snapshot, "^VIX")
    if vix is not None:
        lines.append(f"변동성: VIX {vix:.2f} — 20선 상·하 방향 확인")
    if not lines:
        lines.append("다음 확정세션에서 지수·반도체·금리 방향이 같은 쪽으로 모이는지 확인")
    return lines[:3]


def _symbol_text(symbols: list[Any], *, korean_only: bool = False) -> str:
    values: list[str] = []
    for symbol in symbols:
        ticker = str(getattr(symbol, "ticker", "") or "")
        if korean_only and not ticker.upper().endswith((".KS", ".KQ")):
            continue
        name = str(getattr(symbol, "name", "") or ticker)
        if ticker:
            values.append(f"{name}({ticker})")
    return ", ".join(values)


def _headline_contrast(note_name: str, market_context: dict[str, Any] | None, snapshot: dict[str, Any]) -> str:
    candidates: list[tuple[str, float]] = []
    if note_name.startswith("한국 증시"):
        for key, label in (("kospi_change_pct", "KOSPI"), ("kosdaq_change_pct", "KOSDAQ")):
            value = _safe_float((market_context or {}).get(key))
            if value is not None:
                candidates.append((label, value))
    else:
        for ticker, label in (("^DJI", "다우"), ("^IXIC", "나스닥"), ("^GSPC", "S&P500"), ("^RUT", "러셀2000"), ("^SOX", "반도체")):
            value = _asset_change(snapshot, ticker)
            if value is not None:
                candidates.append((label, value))
    if len(candidates) < 2:
        return str(snapshot.get("flow_proxy") or snapshot.get("regime_label") or "시장 차별화 확인 필요")
    strongest = max(candidates, key=lambda item: item[1])
    weakest = min(candidates, key=lambda item: item[1])
    return f"{strongest[0]} {strongest[1]:+.2f}% vs {weakest[0]} {weakest[1]:+.2f}%"


def _index_lines(note_name: str, market_context: dict[str, Any] | None, snapshot: dict[str, Any]) -> list[str]:
    if _is_us_note(note_name):
        dow = _asset_change(snapshot, "^DJI")
        nasdaq = _asset_change(snapshot, "^IXIC")
        sp500 = _asset_change(snapshot, "^GSPC")
        russell = _asset_change(snapshot, "^RUT")
        sox = _asset_change(snapshot, "^SOX")
        return [
            f"　다우 {_arrow(dow)}　나스닥 {_arrow(nasdaq)}",
            f"　S&P500 {_arrow(sp500)}　러셀2000 {_arrow(russell)}",
            f"　필라델피아 반도체 {_arrow(sox)}",
        ]
    kospi = _safe_float((market_context or {}).get("kospi_change_pct"))
    kosdaq = _safe_float((market_context or {}).get("kosdaq_change_pct"))
    sp500 = _safe_float((market_context or {}).get("sp500_change_pct"))
    nasdaq = _safe_float((market_context or {}).get("nasdaq_change_pct"))
    ewy = _asset_change(snapshot, "EWY")
    usdkrw = _safe_float((market_context or {}).get("usd_krw"))
    lines = [
        f"　KOSPI {_arrow(kospi)}　KOSDAQ {_arrow(kosdaq)}",
        f"　S&P500 {_arrow(sp500)}　나스닥 {_arrow(nasdaq)}",
        f"　한국 ETF(EWY) {_arrow(ewy)}",
    ]
    if usdkrw is not None:
        lines.append(f"　USD/KRW {usdkrw:,.1f}")
    return lines


def _outlook(selected: list[Any], market_context: dict[str, Any] | None, snapshot: dict[str, Any], kind: str, now: datetime):
    from . import consistency_rules, market_outlook

    phase = market_outlook.resolve_market_phase(kind, now)
    news_inputs, sectors = market_outlook._cluster_news_inputs(selected)
    return consistency_rules.consistent_infer_market_outlook(
        phase=phase,
        news_inputs=news_inputs,
        sectors=sectors,
        market_context=market_context,
        global_snapshot=snapshot,
    )


def _flow_summary(snapshot: dict[str, Any]) -> str:
    """Price proxies only. Never present ETF returns as actual fund-flow data."""
    anchor = _asset_session_date(snapshot, "^GSPC") or _asset_session_date(snapshot, "QQQ")
    values = []
    for ticker, label in (
        ("QQQ", "QQQ"),
        ("IWM", "IWM"),
        ("EEM", "EEM"),
        ("HYG", "HYG"),
        ("GLD", "GLD"),
        ("TLT", "TLT"),
    ):
        change = _same_session_change(snapshot, ticker, anchor)
        if change is not None:
            values.append(f"{label} {change:+.2f}%")
    if not values:
        return "ETF 가격 프록시: 동일 세션 비교불가"
    date_text = f" {anchor}" if anchor else ""
    return f"ETF 가격 프록시{date_text}: " + " / ".join(values)


def _macro_snapshot(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    ten_year = _asset_price(snapshot, "^TNX")
    thirty_year = _asset_price(snapshot, "^TYX")
    vix = _asset_price(snapshot, "^VIX")
    oil = _asset_price(snapshot, "CL=F")
    if ten_year is not None:
        parts.append(f"미10년물 {ten_year:.2f}%")
    if thirty_year is not None:
        parts.append(f"미30년물 {thirty_year:.2f}%")
    if vix is not None:
        parts.append(f"VIX {vix:.2f}")
    if oil is not None:
        parts.append(f"WTI ${oil:.2f}")
    return " / ".join(parts) if parts else "금리·변동성·유가 확인불가"


def _market_summary(
    note_name: str,
    payloads: list[dict[str, Any]],
    outlook: Any,
    market_context: dict[str, Any] | None,
    snapshot: dict[str, Any],
) -> list[str]:
    bullets = [
        f"　- 4축 판정 {outlook.verdict}({outlook.score:+d}/10), 글로벌 레짐 {snapshot.get('regime_label', '확인불가')}",
        f"　- {_headline_contrast(note_name, market_context, snapshot)}",
    ]
    if payloads:
        bullets.append(f"　- 핵심 뉴스: {payloads[0]['title']}")
    bullets.append(f"　- 매크로: {_macro_snapshot(snapshot)}")
    bullets.append(f"　- 자금 흐름: {_flow_summary(snapshot)}")
    return bullets[:5]


def _change_factor_lines(index: int, payload: dict[str, Any]) -> list[str]:
    lines = [f"■ 변화 요인 {'①' if index == 1 else '②'} — {payload['title']}"]
    details = _sentences(payload["body"], 3)
    if not details:
        details = [payload["title"]]
    for detail in details:
        lines.append(f"　- {detail}")
    symbols = _symbol_text(payload["symbols"])
    meta = f"[중요도 {payload['score']} · 중요도등급 {payload['grade']}]"
    if symbols:
        lines.append(f"　→ {meta} 직접 언급: {symbols}")
    else:
        lines.append(f"　→ {meta} 직접 언급 종목 없음")
    if payload["url"]:
        lines.append(f"　- 원문: {payload['url']}")
    return lines


def _ranked_sector_groups(payloads: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in payloads:
        for sector in payload["sectors"]:
            if sector not in GENERIC_SECTORS:
                groups[sector].append(payload)
    scored = []
    for sector, items in groups.items():
        unique = list({id(item["cluster"]): item for item in items}.values())
        score = sum(item["score"] for item in unique)
        scored.append((sector, unique, score))
    scored.sort(key=lambda row: row[2], reverse=True)
    return [(sector, sorted(items, key=lambda item: item["score"], reverse=True)) for sector, items, _ in scored[:MAX_NOTE_SECTORS]]


def _sector_lines(sector: str, payloads: list[dict[str, Any]]) -> list[str]:
    lead = payloads[0]["title"] if payloads else "주요 이슈"
    lines = [f"■ {sector} — {_compact(lead, 52)}"]
    for payload in payloads[:4]:
        symbols = _symbol_text(payload["symbols"])
        suffix = f" / {symbols}" if symbols else ""
        lines.append(f"　- [중요도 {payload['score']} · 중요도등급 {payload['grade']}] {payload['title']}{suffix}")
        detail = next(iter(_sentences(payload["body"], 1)), "")
        if detail and detail != payload["title"]:
            lines.append(f"　　{detail}")
    return lines


def _feature_lines(payloads: list[dict[str, Any]], used: set[int]) -> list[str]:
    featured = [payload for payload in payloads if id(payload["cluster"]) not in used and payload["symbols"]]
    if not featured:
        return []
    lines = ["■ 기타 특징주"]
    for payload in featured[:8]:
        lines.append(
            f"　- {_symbol_text(payload['symbols'])}: {payload['title']} [중요도 {payload['score']} · 중요도등급 {payload['grade']}]"
        )
    return lines


def _korea_lines(payloads: list[dict[str, Any]], *, repeat_titles: bool = True) -> list[str]:
    direct: list[str] = []
    derived_sectors: Counter[str] = Counter()
    for payload in payloads:
        kr_symbols = _symbol_text(payload["symbols"], korean_only=True)
        if kr_symbols:
            suffix = f": {payload['title']}" if repeat_titles else " · 상단 핵심요인/섹터에 직접 언급"
            direct.append(f"　- {kr_symbols}{suffix}")
        for sector in payload["sectors"]:
            if sector not in GENERIC_SECTORS:
                derived_sectors[sector] += payload["score"]
    lines = ["■ 한국 증시 관련"]
    if direct:
        lines.extend(list(dict.fromkeys(direct))[:6])
    else:
        lines.append("　- 뉴스 본문에 직접 언급된 한국 상장 종목 없음")
    if derived_sectors:
        sectors = " > ".join(sector for sector, _ in derived_sectors.most_common(4))
        lines.append(f"　- 파생 관찰 섹터(추정): {sectors}")
        lines.append("　　개별 국내 수혜주는 원문 직접 언급이 없으므로 자동 매핑하지 않음")
    return lines


def _judgment_lines(outlook: Any, *, detailed: bool = False) -> list[str]:
    lines = [
        "■ 시황 판정",
        f"　- 최종: {outlook.verdict} | 점수 {outlook.score:+d}/10 | 신뢰도 {outlook.confidence}",
    ]
    if detailed:
        for part in str(outlook.evidence_line or "").split(" | "):
            if part.startswith("축"):
                lines.append(f"　- {part}")
    lines.append(f"　- 상방 확인: {outlook.upside_condition}")
    lines.append(f"　- 하방/무효: {outlook.downside_condition}")
    return lines


def _extract_block(report: str, start_prefix: str, stop_prefixes: tuple[str, ...]) -> list[str]:
    lines = report.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith(start_prefix)), None)
    if start is None:
        return []
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith(stop_prefixes):
            end = index
            break
    return [line for line in lines[start:end] if line.strip()]


def _verification_line(report: str) -> str:
    for line in reversed(report.splitlines()):
        if line.startswith("검증:"):
            return line
    return ""


def _aligned_verification_line(report: str, displayed_count: int) -> str:
    line = _verification_line(report)
    if not line:
        return ""
    return re.sub(
        r"신규\s+\d+개\s+선별",
        f"신규 {max(0, int(displayed_count))}개 선별",
        line,
        count=1,
    )


def _issue_title_keys(report: str) -> list[str]:
    keys: list[str] = []
    for line in report.splitlines():
        match = re.match(r"^\d+\)\s+\[\d{1,3}/[A-D]\]\s+(.+)$", line.strip())
        if not match:
            continue
        key = re.sub(r"[^0-9A-Za-z가-힣]+", "", match.group(1).lower())
        if key:
            keys.append(key[:80])
    return keys


def _payload_matches_keys(payload: dict[str, Any], keys: list[str]) -> bool:
    if not keys:
        return True
    title_key = re.sub(r"[^0-9A-Za-z가-힣]+", "", str(payload.get("title") or "").lower())[:80]
    return any(
        title_key and (title_key in key or key in title_key)
        for key in keys
    )


def _one_line(payloads: list[dict[str, Any]], outlook: Any, snapshot: dict[str, Any]) -> str:
    driver = payloads[0]["title"] if payloads else "뚜렷한 단일 뉴스 촉매 없음"
    risk = "위험회피 지속" if str(snapshot.get("regime")) == "risk_off" else "자금 흐름 확인 필요"
    return f"핵심 변수: {driver}. 4축 판정은 {outlook.verdict}({outlook.score:+d}/10) — {risk}."


def _build_routine_note(
    *,
    original_report: str,
    payloads: list[dict[str, Any]],
    hours: int,
    note_name: str,
    now: datetime,
    market_context: dict[str, Any] | None,
    snapshot: dict[str, Any],
) -> str:
    lines = [
        f"📌 {now:%m/%d} {note_name}",
        "",
        "시장 확인",
        *_index_lines(note_name, market_context, snapshot),
        f"　- {_routine_market_read(snapshot)}",
        f"　- 금리·변동성: {_macro_snapshot(snapshot)}",
        f"　- {_flow_summary(snapshot)}",
    ]
    warning = _market_data_warning(snapshot)
    if warning:
        lines.append(f"　- 데이터 검증: 비정상 프록시 제외 · {warning}")
    lines += [
        "",
        "핵심 변화",
    ]
    if payloads:
        for index, payload in enumerate(payloads[:3], 1):
            lines.append(f"{index}. {payload['title']}")
            lines.append(f"　- 의미: {_why_it_matters(payload)}")
            lines.append(f"　- 근거: {_source_confidence_text(payload)}")
            detail = next(iter(_sentences(payload.get("body"), 1)), "")
            if detail and detail != payload["title"]:
                lines.append(f"　- 확인된 내용: {detail}")
            if payload.get("url"):
                lines.append(f"　- 원문: {payload['url']}")
    else:
        lines.append("　- 새로 확인된 중요 변화 없음")
        lines.append("　- 단일출처 D급·저신뢰 개별실적·반복 뉴스는 알림에서 제외")

    lines += ["", "다음 확인"]
    lines += [f"　- {value}" for value in _routine_watch_lines(snapshot)]

    verification = _aligned_verification_line(original_report, len(payloads))
    if verification:
        lines.extend(["", verification])
    session_date = _asset_session_date(snapshot, "^GSPC")
    session_text = f" · 미국 확정세션 {session_date}" if session_date else ""
    lines.append(
        f"데이터: 최근 {hours}시간 뉴스{session_text} · ETF 수익률은 자금유입/유출이 아니라 가격 프록시"
    )
    note = "\n".join(lines).strip()
    max_chars = min(MAX_NOTE_CHARS, int(getattr(base_report, "MAX_REPORT_CHARS", MAX_NOTE_CHARS)))
    if len(note) > max_chars:
        note = note[: max_chars - 20].rstrip() + "\n… 이하 생략"
    return note


def build_market_note(
    *,
    original_report: str,
    summaries: list[Any],
    hours: int,
    timezone_name: str,
    kind: str,
    now: datetime,
    market_context: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    selected: list[Any] | None = None,
) -> str:
    selected = list(selected) if selected is not None else _selected_clusters(summaries)
    snapshot = _ensure_note_assets(snapshot)
    market_context = _canonical_market_context(market_context, snapshot)
    payloads = sorted((_cluster_payload(cluster) for cluster in selected), key=lambda item: item["score"], reverse=True)
    displayed_keys = _issue_title_keys(original_report)
    if displayed_keys:
        payloads = [payload for payload in payloads if _payload_matches_keys(payload, displayed_keys)]
    note_name = _note_name(kind, now)
    strategy_kind = str(kind or "").lower() in {"strategy_morning", "strategy_evening"}

    if not strategy_kind:
        payloads = [
            payload
            for payload in payloads
            if _routine_payload_allowed(payload, note_name=note_name, snapshot=snapshot)
        ]
        payloads = sorted(payloads, key=lambda item: _routine_payload_rank(item, note_name), reverse=True)[:3]
        return _build_routine_note(
            original_report=original_report,
            payloads=payloads,
            hours=hours,
            note_name=note_name,
            now=now,
            market_context=market_context,
            snapshot=snapshot,
        )

    selected = [payload["cluster"] for payload in payloads]
    outlook = _outlook(selected, market_context, snapshot, kind, now)

    title_driver = payloads[0]["title"] if payloads else f"{outlook.verdict} · 새 중요 뉴스 없음"
    contrast = _headline_contrast(note_name, market_context, snapshot)
    lines = [
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┓",
        f"┃ {now:%m/%d} {note_name} ┃",
        f"┃ {_compact(title_driver, 42)} ┃",
        f"┃ {_compact(contrast, 42)} ┃",
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
        "",
        "📊 마감 지수" if ("클로징" in note_name or note_name.startswith("미 증시 마감")) else "📊 주요 지수",
        *_index_lines(note_name, market_context, snapshot),
        "",
        "■ 장세 요약",
        *_market_summary(note_name, payloads, outlook, market_context, snapshot),
    ]

    macro_first = [
        payload for payload in payloads
        if payload["news_type"] in {"거시", "리스크"} or any(sector in {"거시/정책", "거시"} for sector in payload["sectors"])
    ]
    factors = []
    for payload in macro_first + payloads:
        if id(payload["cluster"]) not in {id(item["cluster"]) for item in factors}:
            factors.append(payload)
        if len(factors) == 2:
            break
    used: set[int] = set()
    for index, payload in enumerate(factors, 1):
        lines.extend(["", *_change_factor_lines(index, payload)])
        used.add(id(payload["cluster"]))

    for sector, sector_payloads in _ranked_sector_groups(payloads):
        visible_payloads = sector_payloads if strategy_kind else [
            item for item in sector_payloads if id(item["cluster"]) not in used
        ]
        if not visible_payloads:
            continue
        lines.extend(["", *_sector_lines(sector, visible_payloads)])
        used.update(id(item["cluster"]) for item in visible_payloads)

    feature_lines = _feature_lines(payloads, used)
    if feature_lines:
        lines.extend(["", *feature_lines])

    if payloads:
        lines.extend(["", *_korea_lines(payloads, repeat_titles=strategy_kind)])
    elif not strategy_kind:
        lines.extend(["", "■ 새 중요 뉴스 없음", "　- 가격·금리·자금 흐름만 갱신. 반복/저중요 뉴스는 본문에서 제외"])
    lines.extend(["", *_judgment_lines(outlook, detailed=strategy_kind)])

    if strategy_kind:
        learning = _extract_block(original_report, "🧠 지속학습 상태", ("🎯", "선별방식:", "📌 핵심 이슈", "검증:"))
        if learning:
            lines.extend(["", *learning])
        strategy = _extract_block(original_report, "🎯", ("선별방식:", "📌 핵심 이슈", "검증:"))
        if strategy:
            lines.extend(["", *strategy])

    lines.extend(["", "📝 한 줄 정리", f"　{_one_line(payloads, outlook, snapshot)}"])
    verification = _aligned_verification_line(original_report, len(payloads))
    if verification:
        lines.extend(["", verification])
    session_date = str(_asset(snapshot, "^GSPC").get("session_date") or "").strip()
    session_text = f" · 미 증시 기준세션 {session_date}" if session_date else ""
    lines.append(
        f"뉴스 범위: 최근 {hours}시간 · 시장 가격: 최근 확정 세션/동일 스냅샷{session_text} · "
        "미확인 옵션/수급 서사 생성 금지"
    )

    note = "\n".join(lines).strip()
    max_chars = min(MAX_NOTE_CHARS, int(getattr(base_report, "MAX_REPORT_CHARS", MAX_NOTE_CHARS)))
    if len(note) > max_chars:
        note = note[: max_chars - 20].rstrip() + "\n… 이하 생략"
    return note


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    current = base_report.build_markdown_report
    if getattr(current, "_market_note_formatter_installed", False):
        _INSTALLED = True
        return
    original = current

    def wrapped(summaries, hours: int, timezone_name: str = "Asia/Seoul") -> str:
        captured: dict[str, Any] = {}
        original_get_market_context = base_report.get_market_context

        def capture_market_context():
            if "market_context" not in captured:
                captured["market_context"] = original_get_market_context()
            return captured["market_context"]

        base_report.get_market_context = capture_market_context
        try:
            report = original(summaries, hours, timezone_name)
        finally:
            base_report.get_market_context = original_get_market_context
        if not report:
            return report

        kind = os.getenv("BRIEFING_KIND", "regular")
        # us_close already comes from market_dashboard_report.build_us_close_dashboard().
        # Re-formatting it here used a second, narrower market context and appended
        # duplicated blocks. Preserve the dedicated dashboard byte-for-byte.
        if str(kind).lower() == "us_close":
            return report

        try:
            from . import consistency_rules

            now = datetime.now(ZoneInfo(timezone_name))
            return build_market_note(
                original_report=report,
                summaries=list(summaries),
                hours=hours,
                timezone_name=timezone_name,
                kind=kind,
                now=now,
                market_context=captured.get("market_context"),
                snapshot=consistency_rules._cached_global_snapshot(),
            )
        except Exception as exc:
            print(f"[market-note] formatter failed: {type(exc).__name__}: {exc}")
            return report

    wrapped._market_note_formatter_installed = True
    wrapped._market_note_formatter_original = original
    base_report.build_markdown_report = wrapped
    app_module = sys.modules.get("telegram_news.app")
    if app_module is not None:
        setattr(app_module, "build_markdown_report", wrapped)
    _INSTALLED = True
    print("[market-note] boxed market note formatter installed")


def install_dispatch_hook() -> None:
    global _DISPATCH_HOOKED
    if _DISPATCH_HOOKED:
        return
    from . import telegram_dispatch

    current = telegram_dispatch._install_generation_pipeline
    if getattr(current, "_market_note_hooked", False):
        _DISPATCH_HOOKED = True
        return

    def wrapped_install_generation_pipeline() -> None:
        current()
        install()

    wrapped_install_generation_pipeline._market_note_hooked = True
    wrapped_install_generation_pipeline._market_note_original = current
    telegram_dispatch._install_generation_pipeline = wrapped_install_generation_pipeline
    _DISPATCH_HOOKED = True


def _patch_messenger_reply_routes(api_module: Any) -> None:
    app = getattr(api_module, "app", None)
    routes = list(getattr(app, "routes", []) or [])
    for route in routes:
        path = str(getattr(route, "path", "") or "")
        methods = set(getattr(route, "methods", set()) or set())
        dependant = getattr(route, "dependant", None)
        if path not in {"/reply", "/api/reply"} or dependant is None:
            continue
        if "GET" in methods:
            def full_reply_get(request, _api=api_module):
                message = _api._query_message(request)
                user_id = _api._query_user(request)
                return _api.answer(message, user_id)[:MESSENGER_NOTE_MAX_CHARS]

            route.endpoint = full_reply_get
            dependant.call = full_reply_get
        elif "POST" in methods:
            async def full_reply_post(request, _api=api_module):
                data = await _api._payload(request)
                message = _api._clean(
                    data.get("message")
                    or data.get("msg")
                    or data.get("text")
                    or data.get("utterance")
                )
                user_id = _api._clean(data.get("sender") or data.get("user_id") or "default-user")
                return _api.answer(message, user_id)[:MESSENGER_NOTE_MAX_CHARS]

            route.endpoint = full_reply_post
            dependant.call = full_reply_post


def install_messenger_bridge(api_module: Any) -> Any:
    if getattr(api_module, "_market_note_bridge_installed", False):
        return api_module
    original_news = api_module._news

    def cached_note_first() -> str:
        try:
            from .report_cache import load_latest_report

            data = load_latest_report()
            report = str(data.get("report") or "").strip()
            if report and ("┏" in report or len(report) >= 100):
                return report
        except Exception:
            pass
        return original_news()

    api_module._news = cached_note_first
    _patch_messenger_reply_routes(api_module)
    api_module._market_note_bridge_installed = True
    return api_module
