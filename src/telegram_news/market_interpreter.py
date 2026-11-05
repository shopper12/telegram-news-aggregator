"""Evidence-first market diagnosis when the optional generative model is unavailable.

Inputs are tagged quote snapshots and dated headlines. We never infer a unique
cause from simultaneous price moves or represent a Yahoo bar as exchange live.
"""
from __future__ import annotations

from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo


def _quote(data: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    q = (data.get("quotes") or {}).get(symbol)
    return q if q and q.get("fresh") and q.get("open") and isinstance(q.get("day_pct"), (int, float)) else None


def _change(data: dict[str, Any], symbol: str) -> float | None:
    row = _quote(data, symbol)
    return float(row["day_pct"]) if row else None


def _fmt(value: float | None) -> str:
    return "미확인" if value is None else f"{value:+.2f}%"


def _credible_headline(headline: dict[str, Any]) -> bool:
    name = str(headline.get("title") or "").lower()
    trusted = ("reuters", "로이터", "연합뉴스", "associated press", "ap news",
               "bloomberg", "블룸버그", "financial times", "wsj", "wall street journal")
    return any(x in name for x in trusted)


def _headlines_section(headlines: list[dict[str, Any]]) -> str:
    relevant = headlines[:3]
    if not relevant:
        return "최근 원인 뉴스: 조회 불가. 가격변화만으로 특정 원인을 확정할 수 없음."
    rows = ["최근 관련 보도(제목과 시각 확인, 인과관계는 별도 검증 필요):"]
    for h in relevant:
        pub = "시각미상"
        try:
            pub = datetime.fromisoformat(str(h.get("published_at") or "")).astimezone(
                ZoneInfo("Asia/Seoul")
            ).strftime("%m/%d %H:%M KST")
        except (ValueError, TypeError):
            pass
        title = str(h.get("title") or "").strip()
        link = str(h.get("link") or "").strip()
        rows.append(f"• {title} [{pub}]" + (f"\n  {link}" if link else ""))
    return "\n".join(rows)


def _news_causes(headlines: list[dict[str, Any]]) -> list[str]:
    """Only expose driver candidates actually mentioned in article headlines."""
    text = " | ".join(str(x.get("title") or "") for x in headlines).lower()
    candidates = [
        (("data center", "data-center", "데이터센터", "ai 규제", "datacenter"), "데이터센터 확대·인허가·정책 관련 불확실성"),
        (("ai spending", "ai 투자", "ai infrastructure", "ai capex", "투자 수익성", "ai 거품"), "AI 인프라 투자 수익성·자본지출 우려"),
        (("qualcomm", "퀄컴", "earnings", "실적 쇼크", "실적 부진"), "반도체 기업 실적/수요 전망 충격"),
        (("federal reserve", "fed ", "연준", "금리 인상", "bond yields"), "금리·통화정책 재평가"),
        (("iran", "이란", "war ", "전쟁", "유가 급등"), "지정학적 위험·유가 불확실성"),
        (("valuation", "고평가", "과열", "ai bubble", "ai 거품", "차익실현"), "AI 주식 밸류에이션·포지셔닝 조정"),
    ]
    return [label for words, label in candidates if any(w in text for w in words)]


def interpret_market(question: str, data: dict[str, Any], headlines: list[dict[str, Any]]) -> str:
    """Grounded, conditional Korean analysis usable without any LLM API key."""
    spy, qqq, soxx = (_change(data, s) for s in ("SPY", "QQQ", "SOXX"))
    tlt, gld, hyg = (_change(data, s) for s in ("TLT", "GLD", "HYG"))
    nvda, tsla = (_change(data, "NVDA"), _change(data, "TSLA"))
    kospi, kosdaq = _change(data, "^KS11"), _change(data, "^KQ11")
    q = question.lower()
    lines: list[str] = ["🔎 [시장 위험 진단 — 관측/해석 구분]"]

    if spy is not None and qqq is not None:
        if spy <= -3 and qqq <= -4:
            lines.append(f"① 관측: S&P500 {_fmt(spy)}, 나스닥100 {_fmt(qqq)}. 광범위한 주식 위험축소 신호(긴급).")
        elif spy <= -1.0 and qqq <= -1.0:
            lines.append(f"① 관측: S&P500 {_fmt(spy)}, 나스닥100 {_fmt(qqq)}. 동반 하락이므로 개별주 악재만은 아님.")
        elif spy >= 1 and qqq >= 1:
            lines.append(f"① 관측: S&P500 {_fmt(spy)}, 나스닥100 {_fmt(qqq)}. 동반 상승(단, 지속성은 미검증).")
        else:
            lines.append(f"① 관측: S&P500 {_fmt(spy)}, 나스닥100 {_fmt(qqq)}. 광범위한 방향성은 미확정.")
        if qqq < spy - 1:
            lines.append(f"• 나스닥100이 S&P500보다 {spy - qqq:.2f}%p 더 하락 → 성장·기술주 매도 집중.")
        if soxx is not None and soxx < qqq - 2:
            lines.append(f"• 반도체(SOXX) {_fmt(soxx)}로 나스닥보다 {qqq - soxx:.2f}%p 추가 약세 → AI/반도체 중심 충격.")
    elif kospi is not None or kosdaq is not None:
        lines.append(f"① 관측: 코스피 {_fmt(kospi)}, 코스닥 {_fmt(kosdaq)}. 미국 지수 현재자료 부재.")
    else:
        lines.append("① 관측: 주요 현물지수의 유효 정규장 시세 부족. 시장 방향 단정 보류.")

    proxies = []
    for name, value in (("장기채(TLT)", tlt), ("금(GLD)", gld), ("하이일드(HYG)", hyg)):
        if value is not None:
            proxies.append(f"{name} {_fmt(value)}")
    if proxies:
        lines.append("② 교차 확인: " + ", ".join(proxies) + ".")
        if tlt is not None and gld is not None and tlt > 0 and gld > 0 and qqq is not None and qqq < -2:
            lines.append("• 주식 하락과 채권·금 동반 상승 → 안전자산 선호와 부합. 자금 유입 규모 자체는 미확인.")
        elif hyg is not None and hyg < -1 and qqq is not None and qqq < -2:
            lines.append("• 하이일드채까지 약세면 신용시장으로 위험회피가 확산될 가능성을 점검해야 함.")
    else:
        lines.append("② 교차 확인: 채권·금·신용시장 최신 정규장 자료 없음.")

    trusted = [h for h in headlines if _credible_headline(h)]
    clues = _news_causes(trusted or headlines)
    if clues:
        evidence_grade = "주요매체 헤드라인" if trusted else "기사 제목(매체 추가검증 필요)"
        lines.append("③ " + evidence_grade + "의 원인 후보: " + "; ".join(clues[:3])
                     + ". 가격 하락과의 단일 인과관계는 확정할 수 없음.")
    else:
        lines.append("③ 원인: 제목으로 확인 가능한 구체적 악재가 부족함. 원문·공시 확인 필요.")

    qqq_row = _quote(data, "QQQ")
    m5 = qqq_row.get("m5_pct") if qqq_row else None
    if m5 is not None and qqq is not None and qqq < -2 and abs(float(m5)) <= .2:
        lines.append(f"④ 단기 변화: 나스닥 하루 {_fmt(qqq)}에도 최근 5분 {_fmt(float(m5))}. "
                     "하락 속도 둔화일 수 있지만 바닥·반등 확인은 아님.")
    elif m5 is not None and float(m5) <= -.8:
        lines.append(f"④ 단기 변화: 나스닥 최근 5분 {_fmt(float(m5))} → 매도 가속 경고.")
    else:
        lines.append("④ 단기 변화: 5분 수치만으로 추세 전환·반등 신호 판정 불가.")

    lines.append("⑤ 다음 점검: QQQ 5·15분 낙폭 확대/축소, SOXX의 상대약세, 반등의 거래량, "
                 "HYG 약세 확산, 주요 뉴스·공시의 원문/시각.")
    if any(w in q for w in ("지금 팔", "매도", "손절", "어떻게", "대응", "사야", "매수", "사는", "살까")):
        lines.append("⑥ 대응 기준: 신용·레버리지/집중보유라면 강제청산 위험과 증거금부터 확인. "
                     "현금·장기 ETF라면 손실 감내범위와 투자기간으로 분할 조정 여부 검토. "
                     "보유종목·비중·평단 부재로 매도/매수 확정 지시는 불가.")
        if nvda is not None or tsla is not None:
            lines.append(f"• 주요 변동: NVDA {_fmt(nvda)}, TSLA {_fmt(tsla)}. "
                         "SOXL·TQQQ 등 레버리지 ETF는 지수보다 손실이 크게 확대될 수 있음.")
    else:
        lines.append("⑥ 행동: 급락 이후 무계획 추격매도·물타기 대신 포지션별 "
                     "레버리지/비중·현금여력 점검. 회복 신호는 거래량과 반도체 상대강도 확인 후 판단.")

    kr_index_row = (data.get("quotes") or {}).get("^KS11") or {}
    if spy is not None and qqq is not None and qqq <= -4 and not kr_index_row.get("open"):
        lines.append("⑦ 한국장 파급: 현 시점 코스피·코스닥은 정규장 실시간 검증 불가. "
                     "다음 개장 때 반도체·AI 관련주 갭하락 위험을 먼저 관찰. "
                     "시초 15분 거래대금, 원/달러, 외국인 선물 수급을 교차 확인. "
                     "실제 갭 크기는 예측 불가.")

    lines.append(_headlines_section(headlines))
    lines.append("※ 시세: Yahoo 비공식 분봉. 최신 갱신시각이 실제 거래소 무지연 시세를 증명하지는 않음.")
    return "\n".join(lines)
