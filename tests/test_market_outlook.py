from datetime import datetime
from zoneinfo import ZoneInfo

from telegram_news.market_outlook import (
    _insert_outlook,
    infer_market_outlook,
    resolve_market_phase,
)


KST = ZoneInfo("Asia/Seoul")


def test_resolve_market_phase_by_explicit_kind_and_clock():
    assert resolve_market_phase("premarket", datetime(2026, 7, 23, 12, 0, tzinfo=KST)) == "장전"
    assert resolve_market_phase("intraday", datetime(2026, 7, 23, 7, 0, tzinfo=KST)) == "장중"
    assert resolve_market_phase("aftermarket", datetime(2026, 7, 23, 10, 0, tzinfo=KST)) == "장후"
    assert resolve_market_phase("regular", datetime(2026, 7, 23, 8, 30, tzinfo=KST)) == "장전"
    assert resolve_market_phase("regular", datetime(2026, 7, 23, 14, 0, tzinfo=KST)) == "장중"
    assert resolve_market_phase("regular", datetime(2026, 7, 23, 16, 0, tzinfo=KST)) == "장후"


def test_infer_market_outlook_detects_positive_news_and_market_flow():
    outlook = infer_market_outlook(
        phase="장전",
        news_inputs=[
            {
                "title": "대형 수주 계약과 실적 상향",
                "text": "공급 계약 체결로 매출 증가와 실적 개선 기대",
                "materiality": 100,
            },
            {
                "title": "외국인 순매수 유입",
                "text": "반도체 중심 강세와 거래대금 확대",
                "materiality": 80,
            },
        ],
        sectors=["반도체", "전기전자"],
        market_context={
            "kospi_change_pct": 0.8,
            "kosdaq_change_pct": 0.6,
            "sp500_change_pct": 0.4,
            "nasdaq_change_pct": 0.7,
            "market_bias": "시장/수급 동반 우호",
        },
    )

    assert outlook.phase == "장전"
    assert outlook.score > 0
    assert outlook.verdict in {"상방 우세", "상방 시도 우세"}
    assert "반도체" in outlook.sector_line
    assert "긍정:" in outlook.evidence_line


def test_infer_market_outlook_detects_negative_risk():
    outlook = infer_market_outlook(
        phase="장후",
        news_inputs=[
            {
                "title": "관세 충돌과 실적 하향",
                "text": "규제 확대와 수요 부진으로 적자 경고",
                "materiality": 100,
            }
        ],
        sectors=["자동차"],
        market_context={
            "kospi_change_pct": -1.1,
            "kosdaq_change_pct": -1.4,
            "sp500_change_pct": -0.5,
            "nasdaq_change_pct": -0.8,
            "market_bias": "시장/수급 동반 약세",
        },
    )

    assert outlook.score < 0
    assert outlook.verdict in {"하방 우세", "하방 경계"}
    assert "부정:" in outlook.evidence_line
    assert "다음 거래일" in outlook.base_scenario


def test_insert_outlook_after_supply_line():
    report = "\n".join(
        [
            "📊 최근 뉴스",
            "시황 1줄: KOSPI +0.50%",
            "수급/시장: 시장 우호",
            "선별방식: 뉴스 중요도",
            "📌 핵심 이슈",
        ]
    )
    section = "🧭 뉴스 기반 장중 시황 추론\n  • 판정: 혼조·중립"

    merged = _insert_outlook(report, section)

    assert merged.index("수급/시장") < merged.index("🧭 뉴스 기반 장중 시황 추론")
    assert merged.index("🧭 뉴스 기반 장중 시황 추론") < merged.index("선별방식")
