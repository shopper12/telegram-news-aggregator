from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
import sys
from typing import Any
from zoneinfo import ZoneInfo

from . import strict_report_v2 as base_report
from .strict_quality import materiality_score


POSITIVE_WORDS = {
    "상승", "강세", "급등", "반등", "돌파", "호재", "수주", "계약", "공급",
    "승인", "허가", "실적 개선", "흑자", "상향", "증가", "확대", "인하",
    "완화", "유입", "순매수", "자사주", "배당", "최고", "회복",
}
NEGATIVE_WORDS = {
    "하락", "약세", "급락", "조정", "이탈", "악재", "규제", "제재", "소송",
    "적자", "하향", "감소", "축소", "취소", "해지", "유출", "순매도",
    "관세", "전쟁", "충돌", "침체", "경고", "부진", "압박", "불확실",
}
RISK_WORDS = {
    "관세", "전쟁", "충돌", "제재", "규제", "금리 인상", "인플레이션",
    "침체", "부도", "파산", "유동성", "공급망", "환율 급등",
}


@dataclass(frozen=True)
class MarketOutlook:
    phase: str
    verdict: str
    score: int
    confidence: str
    sector_line: str
    evidence_line: str
    base_scenario: str
    upside_condition: str
    downside_condition: str


def resolve_market_phase(kind: str, now: datetime) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized in {"kr_premarket", "us_premarket_before", "us_premarket_after", "premarket"}:
        return "장전"
    if normalized in {"intraday"}:
        return "장중"
    if normalized in {"kr_aftermarket", "us_aftermarket", "aftermarket", "overnight"}:
        return "장후"

    minute_of_day = now.hour * 60 + now.minute
    if minute_of_day < 9 * 60:
        return "장전"
    if minute_of_day < 15 * 60 + 30:
        return "장중"
    return "장후"


def _word_hits(text: str, words: set[str]) -> int:
    lower = str(text or "").lower()
    return sum(1 for word in words if word.lower() in lower)


def _market_signal(context: dict[str, Any] | None) -> tuple[int, str, int]:
    if not context:
        return 0, "시장 데이터 미확인", 0

    score = 0
    valid = 0
    parts: list[str] = []
    for key, label in (
        ("kospi_change_pct", "KOSPI"),
        ("kosdaq_change_pct", "KOSDAQ"),
        ("sp500_change_pct", "S&P500"),
        ("nasdaq_change_pct", "Nasdaq"),
    ):
        value = context.get(key)
        if not isinstance(value, (int, float)):
            continue
        valid += 1
        parts.append(f"{label} {value:+.2f}%")
        if value >= 0.3:
            score += 1
        elif value <= -0.3:
            score -= 1

    bias = str(context.get("market_bias") or "")
    if "동반 우호" in bias:
        score += 2
    elif "우호" in bias:
        score += 1
    elif "동반 약세" in bias:
        score -= 2
    elif "중립 이하" in bias:
        score -= 1
    if bias:
        valid += 1
        parts.append(bias)

    return score, " / ".join(parts) if parts else "시장 등락률 미확인", valid


def _cluster_news_inputs(selected: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    inputs: list[dict[str, Any]] = []
    sectors: list[str] = []
    for cluster in selected[:20]:
        try:
            text = base_report._cluster_text(cluster)
            title = base_report._display_title(cluster, 70)
            score = int(materiality_score(cluster))
            cluster_sectors = list(cluster.sectors() or [])
        except Exception:
            continue
        inputs.append({"title": title, "text": text, "materiality": score})
        for sector in cluster_sectors:
            sector_text = str(sector or "").strip()
            if sector_text and sector_text not in sectors:
                sectors.append(sector_text)
    return inputs, sectors


def infer_market_outlook(
    *,
    phase: str,
    news_inputs: list[dict[str, Any]],
    sectors: list[str],
    market_context: dict[str, Any] | None,
) -> MarketOutlook:
    news_score = 0
    positive_drivers: list[tuple[int, str]] = []
    negative_drivers: list[tuple[int, str]] = []
    risk_hits = 0

    for item in news_inputs:
        text = f"{item.get('title') or ''} {item.get('text') or ''}"
        positive = _word_hits(text, POSITIVE_WORDS)
        negative = _word_hits(text, NEGATIVE_WORDS)
        risk_hits += _word_hits(text, RISK_WORDS)
        materiality = max(1, min(5, int(item.get("materiality") or 20) // 20))
        contribution = max(-3, min(3, positive - negative)) * materiality
        news_score += contribution
        title = str(item.get("title") or "").strip()
        if contribution > 0 and title:
            positive_drivers.append((contribution, title))
        elif contribution < 0 and title:
            negative_drivers.append((abs(contribution), title))

    market_score, market_line, market_valid = _market_signal(market_context)
    total_score = max(-10, min(10, news_score + market_score - min(2, risk_hits)))

    if total_score >= 5:
        verdict = "상방 우세"
    elif total_score >= 2:
        verdict = "상방 시도 우세"
    elif total_score <= -5:
        verdict = "하방 우세"
    elif total_score <= -2:
        verdict = "하방 경계"
    else:
        verdict = "혼조·중립"

    evidence_count = len(news_inputs)
    if evidence_count >= 5 and market_valid >= 3:
        confidence = "높음"
    elif evidence_count >= 2 or market_valid >= 2:
        confidence = "보통"
    else:
        confidence = "낮음"

    positive_drivers.sort(reverse=True)
    negative_drivers.sort(reverse=True)
    driver_parts: list[str] = []
    if positive_drivers:
        driver_parts.append("긍정: " + "; ".join(title for _, title in positive_drivers[:2]))
    if negative_drivers:
        driver_parts.append("부정: " + "; ".join(title for _, title in negative_drivers[:2]))
    driver_parts.append("시장: " + market_line)
    evidence_line = " | ".join(driver_parts)
    sector_line = " > ".join(sectors[:4]) if sectors else "뚜렷한 주도 섹터 미확인"

    if phase == "장전":
        base_scenario = (
            f"개장 초반은 {verdict}로 추론한다. 시가 갭보다 첫 30분 거래대금과 "
            "외국인·기관 동반 수급이 뉴스의 실제 가격 반영 여부를 결정할 가능성이 높다."
        )
        upside_condition = "KOSPI·KOSDAQ 시가 유지, 외국인/기관 순매수 개선, 주도 섹터 거래대금 확산"
        downside_condition = "시가 저점 이탈, 원화 약세 확대, 호재 종목의 갭 상승 반납과 시장 폭 악화"
    elif phase == "장중":
        base_scenario = (
            f"현재 장세는 {verdict}로 추론한다. 오전 주도 섹터가 VWAP와 장중 고점을 지키는지, "
            "지수 상승이 일부 대형주에만 집중되는지를 구분해야 한다."
        )
        upside_condition = "지수 고점 재돌파, 상승 종목 수 확대, 외국인 선물·현물 수급 동반 개선"
        downside_condition = "VWAP 이탈 확산, 거래대금 감소 속 지수만 버티는 괴리, 프로그램 순매도 확대"
    else:
        base_scenario = (
            f"종가 기준 뉴스와 수급의 결론은 {verdict}로 추론한다. 장후 뉴스가 기존 주도주를 "
            "재강화하는지, 새로운 섹터로 자금 이동을 만드는지가 다음 거래일의 핵심 변수다."
        )
        upside_condition = "미국 선행시장 우호, 환율 안정, 장후 호재의 복수 출처 확인과 다음 날 거래대금 유입"
        downside_condition = "미국 선행시장 약세, 환율 불안, 장중 강세주의 종가 밀림과 악재 추가 확인"

    return MarketOutlook(
        phase=phase,
        verdict=verdict,
        score=total_score,
        confidence=confidence,
        sector_line=sector_line,
        evidence_line=evidence_line,
        base_scenario=base_scenario,
        upside_condition=upside_condition,
        downside_condition=downside_condition,
    )


def build_market_outlook_section(
    *,
    now: datetime,
    kind: str,
    selected: list[Any],
    market_context: dict[str, Any] | None,
) -> str:
    phase = resolve_market_phase(kind, now)
    news_inputs, sectors = _cluster_news_inputs(selected)
    outlook = infer_market_outlook(
        phase=phase,
        news_inputs=news_inputs,
        sectors=sectors,
        market_context=market_context,
    )
    return "\n".join(
        [
            f"🧭 뉴스 기반 {outlook.phase} 시황 추론",
            f"  • 판정: {outlook.verdict} | 점수 {outlook.score:+d}/10 | 신뢰도 {outlook.confidence}",
            f"  • 근거: {outlook.evidence_line}",
            f"  • 주도 가능 섹터: {outlook.sector_line}",
            f"  • 기본 시나리오: {outlook.base_scenario}",
            f"  • 상방 확인 조건: {outlook.upside_condition}",
            f"  • 하방/무효 조건: {outlook.downside_condition}",
            "  • 주의: 뉴스·지수·수급을 결합한 확률적 추론이며 확정 예측이 아님",
        ]
    )


def _insert_outlook(report: str, section: str) -> str:
    lines = report.splitlines()
    insert_at = None
    for index, line in enumerate(lines):
        if line.startswith("수급/시장:"):
            insert_at = index + 1
            break
    if insert_at is None:
        for index, line in enumerate(lines):
            if line.startswith("선별방식:"):
                insert_at = index
                break
    if insert_at is None:
        insert_at = min(4, len(lines))
    lines[insert_at:insert_at] = [section, ""]
    merged = "\n".join(lines).strip()
    max_chars = int(getattr(base_report, "MAX_REPORT_CHARS", 12000))
    if len(merged) > max_chars:
        return merged[: max_chars - 20] + "\n… 이하 생략"
    return merged


def install() -> None:
    current = base_report.build_markdown_report
    if getattr(current, "_market_outlook_installed", False):
        return

    original = current

    def wrapped_build_markdown_report(summaries, hours: int, timezone_name: str = "Asia/Seoul") -> str:
        captured: dict[str, Any] = {}
        original_get_market_context = base_report.get_market_context

        def capture_market_context() -> dict[str, Any] | None:
            value = original_get_market_context()
            captured["market_context"] = value
            return value

        base_report.get_market_context = capture_market_context
        try:
            report = original(summaries, hours, timezone_name)
        finally:
            base_report.get_market_context = original_get_market_context

        if not report:
            return report

        try:
            selected, *_ = base_report.s._select_strict(summaries)
            selected = base_report._drop_noise(selected)
        except Exception:
            selected = []
        now = datetime.now(ZoneInfo(timezone_name))
        kind = os.getenv("BRIEFING_KIND", "regular")
        section = build_market_outlook_section(
            now=now,
            kind=kind,
            selected=selected,
            market_context=captured.get("market_context"),
        )
        return _insert_outlook(report, section)

    wrapped_build_markdown_report._market_outlook_installed = True
    wrapped_build_markdown_report._market_outlook_original = original
    base_report.build_markdown_report = wrapped_build_markdown_report

    app_module = sys.modules.get("telegram_news.app")
    if app_module is not None:
        setattr(app_module, "build_markdown_report", wrapped_build_markdown_report)

    print("[market-outlook] phase-aware premarket/intraday/aftermarket inference installed")
