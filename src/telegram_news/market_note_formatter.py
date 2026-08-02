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
MAX_NOTE_SECTORS = int(os.getenv("MARKET_NOTE_MAX_SECTORS", "3"))
MAX_NOTE_CHARS = int(os.getenv("MAX_REPORT_CHARS", "12000"))
MESSENGER_NOTE_MAX_CHARS = int(os.getenv("MESSENGER_NOTE_MAX_CHARS", "3500"))
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
    return _safe_float(_asset(snapshot, ticker).get("change_pct"))


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


def _note_name(kind: str, now: datetime) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized == "strategy_morning":
        return "미 증시 클로징 노트"
    if normalized == "strategy_evening":
        return "미 증시 오프닝 전략"
    if normalized in {"us_premarket_before", "us_premarket_after"}:
        return "미 증시 프리마켓 노트"
    if normalized in {"kr_premarket", "premarket"}:
        return "한국 증시 장전 노트"
    if normalized == "intraday":
        return "한국 증시 장중 노트"
    if normalized in {"kr_aftermarket", "aftermarket"}:
        return "한국 증시 클로징 노트"
    if normalized == "overnight":
        return "글로벌 야간 시황 노트"
    minute = now.hour * 60 + now.minute
    if minute < 9 * 60:
        return "글로벌 장전 브리핑"
    if minute < 15 * 60 + 30:
        return "한국 증시 장중 노트"
    return "글로벌 마감 시황 노트"


def _is_us_note(note_name: str) -> bool:
    return note_name.startswith("미 증시")


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
        "title": _compact(getattr(item, "title", ""), 88),
        "body": _compact(getattr(item, "body", ""), 500),
        "news_type": str(getattr(best, "news_type", "") or ""),
        "score": score,
        "grade": grade,
        "sectors": sectors,
        "symbols": _symbols(cluster),
        "url": _source_url(cluster),
    }


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
    if _is_us_note(note_name):
        for ticker, label in (("^DJI", "다우"), ("^IXIC", "나스닥"), ("^GSPC", "S&P500"), ("^RUT", "러셀2000"), ("^SOX", "반도체")):
            value = _asset_change(snapshot, ticker)
            if value is not None:
                candidates.append((label, value))
    else:
        for key, label in (("kospi_change_pct", "KOSPI"), ("kosdaq_change_pct", "KOSDAQ"), ("sp500_change_pct", "S&P500"), ("nasdaq_change_pct", "Nasdaq")):
            value = _safe_float((market_context or {}).get(key))
            if value is not None:
                candidates.append((label, value))
    if len(candidates) < 2:
        return str(snapshot.get("flow_proxy") or snapshot.get("regime_label") or "시장 차별화 확인 필요")
    strongest = max(candidates, key=lambda item: item[1])
    weakest = min(candidates, key=lambda item: item[1])
    return f"{strongest[0]} {strongest[1]:+.2f}% 견인 vs {weakest[0]} {weakest[1]:+.2f}% 차별화"


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
    flow = str(snapshot.get("flow_proxy") or "").strip()
    if flow:
        return flow
    values = []
    for ticker, label in (("QQQ", "성장주"), ("EEM", "신흥국"), ("HYG", "하이일드"), ("GLD", "금"), ("TLT", "장기채")):
        change = _asset_change(snapshot, ticker)
        if change is not None:
            values.append(f"{label} {change:+.2f}%")
    return " / ".join(values) if values else "자금 흐름 프록시 확인불가"


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
    bullets.append(f"　- 자금 흐름: {_flow_summary(snapshot)}")
    return bullets[:4]


def _change_factor_lines(index: int, payload: dict[str, Any]) -> list[str]:
    lines = [f"■ 변화 요인 {'①' if index == 1 else '②'} — {payload['title']}"]
    details = _sentences(payload["body"], 3)
    if not details:
        details = [payload["title"]]
    for detail in details:
        lines.append(f"　- {detail}")
    symbols = _symbol_text(payload["symbols"])
    meta = f"[{payload['score']}/{payload['grade']}]"
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
        lines.append(f"　- [{payload['score']}/{payload['grade']}] {payload['title']}{suffix}")
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
            f"　- {_symbol_text(payload['symbols'])}: {payload['title']} [{payload['score']}/{payload['grade']}]"
        )
    return lines


def _korea_lines(payloads: list[dict[str, Any]]) -> list[str]:
    direct: list[str] = []
    derived_sectors: Counter[str] = Counter()
    for payload in payloads:
        kr_symbols = _symbol_text(payload["symbols"], korean_only=True)
        if kr_symbols:
            direct.append(f"　- {kr_symbols}: {payload['title']}")
        for sector in payload["sectors"]:
            if sector not in GENERIC_SECTORS:
                derived_sectors[sector] += payload["score"]
    lines = ["■ 한국 증시 관련"]
    if direct:
        lines.extend(direct[:6])
    else:
        lines.append("　- 뉴스 본문에 직접 언급된 한국 상장 종목 없음")
    if derived_sectors:
        sectors = " > ".join(sector for sector, _ in derived_sectors.most_common(4))
        lines.append(f"　- 파생 관찰 섹터(추정): {sectors}")
        lines.append("　　개별 국내 수혜주는 원문 직접 언급이 없으므로 자동 매핑하지 않음")
    return lines


def _judgment_lines(outlook: Any) -> list[str]:
    lines = [
        "■ 시황 판정",
        f"　- 최종: {outlook.verdict} | 점수 {outlook.score:+d}/10 | 신뢰도 {outlook.confidence}",
    ]
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
    return f"{driver}가 핵심 변수이며, 4축 판정은 {outlook.verdict}({outlook.score:+d}/10) — {risk}."


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
    payloads = sorted((_cluster_payload(cluster) for cluster in selected), key=lambda item: item["score"], reverse=True)
    displayed_keys = _issue_title_keys(original_report)
    if displayed_keys:
        payloads = [payload for payload in payloads if _payload_matches_keys(payload, displayed_keys)]
        selected = [payload["cluster"] for payload in payloads]
    outlook = _outlook(selected, market_context, snapshot, kind, now)
    note_name = _note_name(kind, now)

    title_driver = payloads[0]["title"] if payloads else f"{outlook.verdict} · 신규 핵심 이슈 제한"
    contrast = _headline_contrast(note_name, market_context, snapshot)
    lines = [
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┓",
        f"┃ {now:%m/%d} {note_name} ┃",
        f"┃ {_compact(title_driver, 42)} ┃",
        f"┃ {_compact(contrast, 42)} ┃",
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
        "",
        "📊 마감 지수" if "클로징" in note_name or "마감" in note_name else "📊 주요 지수",
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
        lines.extend(["", *_sector_lines(sector, sector_payloads)])
        used.update(id(item["cluster"]) for item in sector_payloads)

    feature_lines = _feature_lines(payloads, used)
    if feature_lines:
        lines.extend(["", *feature_lines])

    lines.extend(["", *_korea_lines(payloads)])
    lines.extend(["", *_judgment_lines(outlook)])

    learning = _extract_block(original_report, "🧠 지속학습 상태", ("🎯", "선별방식:", "📌 핵심 이슈", "검증:"))
    if learning:
        lines.extend(["", *learning])
    strategy = _extract_block(original_report, "🎯", ("선별방식:", "📌 핵심 이슈", "검증:"))
    if strategy:
        lines.extend(["", *strategy])

    lines.extend(["", "📝 한 줄 정리", f"　{_one_line(payloads, outlook, snapshot)}"])
    verification = _verification_line(original_report)
    if verification:
        lines.extend(["", verification])
    lines.append(f"데이터 범위: 최근 {hours}시간 · 확인된 뉴스와 시장 데이터만 사용 · 미확인 옵션/수급 서사 생성 금지")

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
        try:
            from . import consistency_rules

            now = datetime.now(ZoneInfo(timezone_name))
            return build_market_note(
                original_report=report,
                summaries=list(summaries),
                hours=hours,
                timezone_name=timezone_name,
                kind=os.getenv("BRIEFING_KIND", "regular"),
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
