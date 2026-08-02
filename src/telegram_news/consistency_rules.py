from __future__ import annotations

from time import monotonic
import re
from typing import Any

from . import adaptive_strategy, extractor, market_outlook, strict_quality
from . import strict_report, strict_report_v2, summarizer, symbol_resolver

FINANCIAL_METRIC_ACRONYMS = {
    "ARR", "RPO", "MRR", "NRR", "FCF", "EBITDA", "EPS", "ROE", "ROA",
    "GMV", "ASP", "TAM", "SAM", "SOM", "YOY", "QOQ",
}
US_EXCHANGE_BY_TICKER = {
    "AAPL": "NASDAQ", "AMD": "NASDAQ", "AMZN": "NASDAQ", "AVGO": "NASDAQ",
    "GOOGL": "NASDAQ", "META": "NASDAQ", "MSFT": "NASDAQ", "MU": "NASDAQ",
    "NFLX": "NASDAQ", "NVDA": "NASDAQ", "PLTR": "NASDAQ", "SMCI": "NASDAQ",
    "TSLA": "NASDAQ", "ORCL": "NYSE", "NOW": "NYSE", "IBM": "NYSE",
    "LLY": "NYSE", "NVO": "NYSE", "TSM": "NYSE",
}
MACRO_SHOCK_WORDS = {
    "fomc 결정", "기준금리 결정", "금리 인상", "금리 인하", "전쟁 발발",
    "군사 충돌", "금융위기", "은행 파산", "국가부도", "유동성 위기",
    "관세 전면", "비상조치",
}
MAJOR_EVENT_WORDS = {
    "실적 서프라이즈", "어닝 서프라이즈", "가이던스 상향", "가이던스 하향",
    "핵심 정책", "정부 발표", "정책 발표", "규제 발표", "fda 승인",
    "품목허가", "대형주 실적",
}
SECTOR_EVENT_WORDS = {
    "목표가", "산업 리포트", "섹터 리포트", "공급망", "수주", "계약",
    "납품", "증설", "감산", "생산 차질",
}
LOW_CONFIDENCE_WORDS = {
    "루머", "가능성", "추정", "출처 불명", "찌라시", "관계자에 따르면",
}
_INSTALLED = False
_SNAPSHOT_CACHE: dict[str, Any] | None = None
_SNAPSHOT_CACHE_AT = 0.0

_ORIGINAL_RESOLVE_SYMBOLS = symbol_resolver.resolve_symbols
_ORIGINAL_EXTRACT_SIGNALS = extractor.extract_signals
_ORIGINAL_MATERIALITY_SCORE = strict_quality.materiality_score
_ORIGINAL_LOCAL_INSIGHT_REPORT = strict_report_v2._local_insight_report
_ORIGINAL_IS_DISPLAY_NOISE = strict_report_v2._is_display_noise
_ORIGINAL_BUILD_STRATEGY_SECTION = adaptive_strategy.build_strategy_section


def _cached_global_snapshot() -> dict[str, Any]:
    global _SNAPSHOT_CACHE, _SNAPSHOT_CACHE_AT
    now = monotonic()
    if _SNAPSHOT_CACHE is not None and now - _SNAPSHOT_CACHE_AT < 120:
        return _SNAPSHOT_CACHE
    from .global_market_tracker import collect_global_snapshot
    _SNAPSHOT_CACHE = collect_global_snapshot()
    _SNAPSHOT_CACHE_AT = now
    return _SNAPSHOT_CACHE


def _safe_resolve_symbols(text: str, categories=None, raw_tickers=None):
    explicit = {
        ticker for ticker in symbol_resolver._strict_us_tickers(text)
        if ticker not in FINANCIAL_METRIC_ACRONYMS
    }
    resolved = _ORIGINAL_RESOLVE_SYMBOLS(
        text,
        categories=categories,
        raw_tickers=sorted(explicit),
    )
    return [
        symbol for symbol in resolved
        if str(symbol.ticker).upper() not in FINANCIAL_METRIC_ACRONYMS
    ]


def _safe_extract_signals(text: str, repeat_count: int = 1, market_type: str = "KR"):
    original = _ORIGINAL_EXTRACT_SIGNALS(text, repeat_count=repeat_count, market_type=market_type)
    categories = ["crypto"] if market_type.upper() == "CRYPTO" else ["us_stock"] if market_type.upper() == "US" else ["kr_stock"]
    resolved = _safe_resolve_symbols(text, categories=categories)
    if market_type.upper() == "CRYPTO":
        tickers = [item.ticker for item in resolved if item.asset_type == "crypto"]
    else:
        tickers = [item.ticker for item in resolved if item.asset_type != "crypto"]
    return extractor.ExtractedSignal(
        sectors=original.sectors,
        keywords=original.keywords,
        tickers=sorted(set(tickers)),
        importance_score=original.importance_score,
    )


def _cluster_urls(cluster: Any) -> set[str]:
    urls: set[str] = set()
    for news in getattr(cluster, "items", []) or []:
        item = getattr(news, "item", None)
        for value in getattr(item, "source_urls", []) or []:
            text = str(value or "").strip()
            if text.startswith(("http://", "https://")) and "t.me/" not in text and "telegram.me/" not in text:
                urls.add(text)
    return urls


def source_grade(cluster: Any) -> str:
    urls = _cluster_urls(cluster)
    channel_count = int(cluster.channel_count()) if hasattr(cluster, "channel_count") else 0
    item_count = len(getattr(cluster, "items", []) or [])
    if len(urls) >= 2:
        return "A"
    if urls:
        return "B"
    if channel_count >= 2 or item_count >= 2:
        return "C"
    return "D"


def _scaled(raw: int, low: int, high: int) -> int:
    ratio = max(0.0, min(1.0, float(raw) / 100.0))
    return int(round(low + (high - low) * ratio))


def consistent_materiality_score(cluster: Any) -> int:
    raw = int(_ORIGINAL_MATERIALITY_SCORE(cluster))
    best = cluster.best()
    text = f"{best.item.title} {best.item.body}".lower()
    grade = source_grade(cluster)
    news_type = str(getattr(best, "news_type", "") or "")
    has_number = bool(strict_quality.NUMBER_EVIDENCE_RE.search(text))
    has_confirmation = strict_quality._has_any(text, strict_quality.CONFIRMATION_WORDS)
    repeated = (hasattr(cluster, "channel_count") and cluster.channel_count() >= 2) or len(getattr(cluster, "items", []) or []) >= 2
    strong_macro = news_type in {"거시", "리스크"} and any(word in text for word in MACRO_SHOCK_WORDS)
    major_event = any(word in text for word in MAJOR_EVENT_WORDS) or (news_type == "실적" and has_number) or (news_type in {"거시", "리스크"} and has_confirmation and has_number)
    sector_event = any(word in text for word in SECTOR_EVENT_WORDS) or news_type in {"이벤트", "실적"}
    simple_disclosure = news_type == "공시/확정" and not has_number and not any(word in text for word in {"수주", "계약", "실적", "승인", "허가"})
    low_confidence = any(word in text for word in LOW_CONFIDENCE_WORDS)

    if low_confidence or simple_disclosure:
        return min(39, _scaled(raw, 10, 39))
    if strong_macro:
        if grade == "A" and raw >= 90:
            return 100
        return min(99, _scaled(raw, 90, 99))
    if major_event:
        return min(99, _scaled(raw, 80, 99))
    if sector_event:
        return _scaled(raw, 60, 79)
    if repeated or "ir" in text or "후속" in text:
        return _scaled(raw, 40, 59)
    if news_type in {"가격반응", "테마", "정보"}:
        return min(59, _scaled(raw, 30, 59))
    return _scaled(raw, 40, 59)


def consistent_materiality_grade(cluster: Any) -> str:
    return source_grade(cluster)


def _consistent_is_display_noise(cluster: Any) -> bool:
    best = cluster.best()
    if consistent_materiality_grade(cluster) == "D" and str(getattr(best, "news_type", "")) == "테마" and not strict_report_v2._display_symbols(cluster):
        return True
    return _ORIGINAL_IS_DISPLAY_NOISE(cluster)


def _exchange_label(ticker: str) -> str:
    upper = str(ticker or "").upper()
    if upper.endswith(".KS"):
        return "KOSPI"
    if upper.endswith(".KQ"):
        return "KOSDAQ"
    return US_EXCHANGE_BY_TICKER.get(upper.split(".")[0], "미국시장·거래소 원문 미확인")


def _format_related_report(report: str) -> str:
    lines: list[str] = []
    perfect_a = 0
    for raw_line in report.splitlines():
        line = raw_line
        marker = re.search(r"\[(\d{1,3})/([A-D])\]", line)
        if marker and marker.group(1) == "100" and marker.group(2) == "A":
            perfect_a += 1
            if perfect_a > 2:
                line = line.replace("[100/A]", "[99/A]", 1)
        if line.startswith("  • 관련종목:"):
            value = line.split(":", 1)[1].strip()
            if value == "직접 언급 없음":
                lines.append("  • 관련종목: 직접 언급 없음 (국내 수혜주 추정 불가)")
                continue
            formatted: list[str] = []
            global_only = True
            for part in [item.strip() for item in value.split(", ") if item.strip()]:
                match = re.match(r"^(.*)\(([^()]+)\)$", part)
                if not match:
                    formatted.append(part)
                    continue
                name, ticker = match.group(1).strip(), match.group(2).strip()
                exchange = _exchange_label(ticker)
                if exchange in {"KOSPI", "KOSDAQ"}:
                    global_only = False
                formatted.append(f"{name}({ticker}, {exchange})")
            lines.append("  • 관련종목: " + ", ".join(formatted))
            if formatted and global_only:
                lines.append("  • 국내 ETF 연계: 직접 연계 여부 별도 확인 (국내 수혜주 추정 금지)")
            continue
        lines.append(line)
    return "\n".join(lines)


def _consistent_local_insight_report(**kwargs: Any) -> str:
    return _format_related_report(_ORIGINAL_LOCAL_INSIGHT_REPORT(**kwargs))


def _index_bucket(value: Any) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number <= -5:
        return -3
    if number <= -2:
        return -1
    if number < 0:
        return 0
    if number >= 5:
        return 3
    if number >= 2:
        return 2
    if number > 0:
        return 1
    return 0


def _index_axis(context: dict[str, Any] | None) -> tuple[float, str, int]:
    labels = (("kospi_change_pct", "KOSPI"), ("kosdaq_change_pct", "KOSDAQ"), ("sp500_change_pct", "S&P500"), ("nasdaq_change_pct", "Nasdaq"))
    values: list[int] = []
    parts: list[str] = []
    for key, label in labels:
        value = (context or {}).get(key)
        bucket = _index_bucket(value)
        if bucket is None:
            continue
        values.append(bucket)
        parts.append(f"{label} {float(value):+.2f}%→{bucket:+d}")
    axis = sum(values) / len(values) if values else 0.0
    return axis, " / ".join(parts) if parts else "지수 데이터 미확인→0", len(values)


def _regime_axis(snapshot: dict[str, Any] | None) -> tuple[int, str, int]:
    regime = str((snapshot or {}).get("regime") or "")
    if regime == "risk_off":
        return -3, "위험회피→-3", 1
    if regime == "risk_on":
        return 3, "위험선호→+3", 1
    if regime == "mixed":
        return 0, "중립→0", 1
    return 0, "글로벌 레짐 미확인→0", 0


def _asset_change(snapshot: dict[str, Any] | None, ticker: str) -> float | None:
    value = (((snapshot or {}).get("assets") or {}).get(ticker) or {}).get("change_pct")
    return float(value) if isinstance(value, (int, float)) else None


def _flow_axis(snapshot: dict[str, Any] | None) -> tuple[int, str, int]:
    risk_values = {"성장주": _asset_change(snapshot, "QQQ"), "신흥국": _asset_change(snapshot, "EEM"), "하이일드": _asset_change(snapshot, "HYG")}
    weak_count = sum(value is not None and value < -3 for value in risk_values.values())
    score = -2 if weak_count >= 2 else 0
    gold = _asset_change(snapshot, "GLD")
    bonds = _asset_change(snapshot, "TLT")
    forced_liquidation = gold is not None and bonds is not None and gold < 0 and bonds < 0
    if forced_liquidation:
        score -= 1
    parts = [f"{label} {value:+.2f}%" for label, value in risk_values.items() if value is not None]
    if gold is not None:
        parts.append(f"금 {gold:+.2f}%")
    if bonds is not None:
        parts.append(f"장기채 {bonds:+.2f}%")
    if forced_liquidation:
        parts.append("금·장기채 동반 하락→-1")
    parts.append(f"축점수 {score:+d}")
    valid = sum(value is not None for value in risk_values.values()) + int(gold is not None and bonds is not None)
    return max(-3, min(3, score)), " / ".join(parts), valid


def _news_axis(news_inputs: list[dict[str, Any]], sectors: list[str]):
    positive_weight = 0.0
    negative_weight = 0.0
    positive_drivers: list[tuple[float, str]] = []
    negative_drivers: list[tuple[float, str]] = []
    for item in news_inputs:
        text = f"{item.get('title') or ''} {item.get('text') or ''}"
        positive = market_outlook._word_hits(text, market_outlook.POSITIVE_WORDS)
        negative = market_outlook._word_hits(text, market_outlook.NEGATIVE_WORDS)
        weight = max(0.1, min(1.0, float(item.get("materiality") or 0) / 100.0))
        title = str(item.get("title") or "").strip()
        if positive > negative:
            contribution = (positive - negative) * weight
            positive_weight += contribution
            if title:
                positive_drivers.append((contribution, title))
        elif negative > positive:
            contribution = (negative - positive) * weight
            negative_weight += contribution
            if title:
                negative_drivers.append((contribution, title))
    total = positive_weight + negative_weight
    if total <= 0:
        score, ratio = 0, 0.5
    else:
        ratio = positive_weight / total
        if len(sectors) >= 2 and positive_weight > negative_weight:
            ratio = min(1.0, ratio + 0.05)
        score = 3 if ratio >= 0.70 else 2 if ratio >= 0.60 else 1 if ratio > 0.50 else -3 if ratio <= 0.30 else -2 if ratio <= 0.40 else -1 if ratio < 0.50 else 0
    detail = f"긍정비중 {ratio * 100:.0f}% / 긍정 {positive_weight:.2f} / 부정 {negative_weight:.2f} / 섹터 {len(sectors)}개→{score:+d}"
    positive_drivers.sort(reverse=True)
    negative_drivers.sort(reverse=True)
    return score, detail, positive_drivers, negative_drivers


def consistent_infer_market_outlook(*, phase: str, news_inputs: list[dict[str, Any]], sectors: list[str], market_context: dict[str, Any] | None, global_snapshot: dict[str, Any] | None = None):
    snapshot = global_snapshot if global_snapshot is not None else _cached_global_snapshot()
    index_score, index_detail, index_valid = _index_axis(market_context)
    regime_score, regime_detail, regime_valid = _regime_axis(snapshot)
    flow_score, flow_detail, flow_valid = _flow_axis(snapshot)
    news_score, news_detail, positive_drivers, negative_drivers = _news_axis(news_inputs, sectors)
    weighted = index_score * 0.30 + regime_score * 0.30 + flow_score * 0.20 + news_score * 0.20
    total_score = int(round(max(-10.0, min(10.0, weighted / 3.0 * 10.0))))
    verdict = "상방 우세" if total_score >= 5 else "하방 우세 / 위험회피 모드" if total_score <= -5 else "중립/혼조"
    valid_axes = int(index_valid > 0) + regime_valid + int(flow_valid >= 2) + int(bool(news_inputs))
    confidence = "높음" if valid_axes == 4 else "보통" if valid_axes >= 2 else "낮음"
    drivers: list[str] = []
    if positive_drivers:
        drivers.append("긍정: " + "; ".join(title for _, title in positive_drivers[:2]))
    if negative_drivers:
        drivers.append("부정: " + "; ".join(title for _, title in negative_drivers[:2]))
    drivers.extend([
        f"축1 지수(30%) {index_score:+.2f}: {index_detail}",
        f"축2 레짐(30%) {regime_score:+d}: {regime_detail}",
        f"축3 흐름(20%) {flow_score:+d}: {flow_detail}",
        f"축4 뉴스(20%) {news_score:+d}: {news_detail}",
    ])
    sector_line = " > ".join(sectors[:4]) if sectors else "뚜렷한 주도 섹터 미확인"
    if phase == "장전":
        base_scenario = f"개장 초반 판정은 {verdict}다. 첫 30분 지수 방향, 외국인·기관 수급, 주도 섹터 거래대금이 4축 판정과 일치하는지 확인한다."
        upside_condition = "주요 지수 시가 유지, 위험선호 지속, 성장주·신흥국·하이일드 회복"
        downside_condition = "지수 저점 이탈, 위험회피 지속, 성장주·신흥국·하이일드 동반 급락"
    elif phase == "장중":
        base_scenario = f"장중 판정은 {verdict}다. VWAP, 시장 폭, 프로그램 수급이 4축 점수와 반대로 움직이면 신규 추격을 보류한다."
        upside_condition = "지수 고점 재돌파, 상승 종목 수 확대, 위험자산 프록시 낙폭 축소"
        downside_condition = "VWAP 이탈 확산, 시장 폭 악화, 금·장기채 동반 하락"
    else:
        base_scenario = f"종가 기준 판정은 {verdict}다. 미국 선행시장과 다음 거래일 장전 데이터로 4축을 다시 계산해 동일 방향인지 검증한다."
        upside_condition = "미국 지수·위험선호·성장주 프록시 동반 개선"
        downside_condition = "미국 지수 약세, 위험회피 고정, 강제 청산 프록시 확인"
    return market_outlook.MarketOutlook(
        phase=phase,
        verdict=verdict,
        score=total_score,
        confidence=confidence,
        sector_line=sector_line,
        evidence_line=" | ".join(drivers),
        base_scenario=base_scenario,
        upside_condition=upside_condition,
        downside_condition=downside_condition,
    )


def consistent_build_market_outlook_section(*, now: Any, kind: str, selected: list[Any], market_context: dict[str, Any] | None) -> str:
    phase = market_outlook.resolve_market_phase(kind, now)
    news_inputs, sectors = market_outlook._cluster_news_inputs(selected)
    outlook = consistent_infer_market_outlook(
        phase=phase,
        news_inputs=news_inputs,
        sectors=sectors,
        market_context=market_context,
        global_snapshot=_cached_global_snapshot(),
    )
    return "\n".join([
        f"🧭 뉴스 기반 {outlook.phase} 시황 추론",
        f"  • 판정: {outlook.verdict} | 점수 {outlook.score:+d}/10 | 신뢰도 {outlook.confidence}",
        f"  • 4축 근거: {outlook.evidence_line}",
        f"  • 주도 가능 섹터: {outlook.sector_line}",
        f"  • 기본 시나리오: {outlook.base_scenario}",
        f"  • 상방 확인 조건: {outlook.upside_condition}",
        f"  • 하방/무효 조건: {outlook.downside_condition}",
        "  • 산식: 지수 30% + 글로벌 레짐 30% + 자금 흐름 20% + 뉴스 20%",
        "  • 주의: 확률적 추론이며 확정 예측이 아님",
    ])


def _consistent_build_strategy_section(*args: Any, **kwargs: Any) -> str:
    text = _ORIGINAL_BUILD_STRATEGY_SECTION(*args, **kwargs)
    ledger = kwargs.get("ledger") if "ledger" in kwargs else args[3]
    recommendations = kwargs.get("recommendations") if "recommendations" in kwargs else args[5]
    state = kwargs.get("state") if "state" in kwargs else args[2]
    stats = (state or {}).get("stats") or {}
    has_strategy_data = bool((ledger or {}).get("recommendations") or recommendations or int(stats.get("evaluated_24h") or 0))
    if has_strategy_data:
        return text
    hidden_prefixes = ("🧠 지속학습 상태", "  • 뉴스 메모리:", "  • 전략 원장:", "  • 누적 성과:")
    return "\n".join(line for line in text.splitlines() if not line.startswith(hidden_prefixes)).replace("\n\n\n", "\n\n")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    symbol_resolver.resolve_symbols = _safe_resolve_symbols
    extractor.resolve_symbols = _safe_resolve_symbols
    extractor.extract_signals = _safe_extract_signals
    summarizer.extract_signals = _safe_extract_signals
    strict_quality.materiality_score = consistent_materiality_score
    strict_quality.materiality_grade = consistent_materiality_grade
    strict_report.materiality_score = consistent_materiality_score
    strict_report.materiality_grade = consistent_materiality_grade
    strict_report_v2.materiality_score = consistent_materiality_score
    strict_report_v2.materiality_grade = consistent_materiality_grade
    strict_report_v2._is_display_noise = _consistent_is_display_noise
    strict_report_v2._local_insight_report = _consistent_local_insight_report
    market_outlook.infer_market_outlook = consistent_infer_market_outlook
    market_outlook.build_market_outlook_section = consistent_build_market_outlook_section
    adaptive_strategy.collect_global_snapshot = _cached_global_snapshot
    adaptive_strategy.build_strategy_section = _consistent_build_strategy_section
    _INSTALLED = True
    print("[consistency-rules] market=4-axis symbols=explicit-only materiality=0-100+A-D learning=hide-empty")
