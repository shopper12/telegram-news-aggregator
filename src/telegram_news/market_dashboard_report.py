from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
import re

import requests

from . import strict_report as strict
from . import strict_report_v2 as display
from .market_dashboard_data import get_market_dashboard_context
from .strict_quality import materiality_grade, materiality_score
from .summarizer import SummaryItem


DEFAULT_MODEL = "gemini-2.5-flash"
MAX_REPORT_CHARS = int(os.getenv("US_CLOSE_MAX_REPORT_CHARS", "9000"))
FORBIDDEN = ["비트코인", "이더리움", "업비트", "바이낸스", "목표가", "손절가", "무조건 매수", "확정 상승"]


def _issue_payload(clusters: list, now: datetime) -> list[dict]:
    rows = []
    for idx, cluster in enumerate(clusters[:20], 1):
        best = cluster.best()
        symbols = display._display_symbols(cluster)
        rows.append(
            {
                "rank": idx,
                "importance": materiality_score(cluster),
                "grade": materiality_grade(cluster),
                "type": best.news_type,
                "title": display._display_title(cluster, 120),
                "body": strict.base._short(best.item.body or "", 420),
                "sectors": cluster.sectors()[:5],
                "symbols": [{"name": str(s.name or ""), "ticker": s.ticker} for s in symbols],
                "age": display._age_line(cluster, now),
                "source_url": display._source_url(cluster),
            }
        )
    return rows


def _prompt(payload: dict) -> str:
    return (
        "너는 한국 투자자를 위한 글로벌 크로스애셋 시장 데스크 + 뉴스 편집장 + 데이터 검증 담당자다.\n"
        "목표는 미국장 마감 후 시장을 움직인 원인을 찾아 한국장에 연결하는 것이다.\n\n"
        "절대 규칙:\n"
        "1. INPUT_JSON에 존재하는 사실과 숫자만 사용한다. 숫자·실적·가이던스·경제일정을 추정하지 않는다.\n"
        "2. 값이 없거나 충돌하면 '확인불가'라고 쓴다.\n"
        "3. 급등·급락 자체를 원인으로 쓰지 않는다. 촉매가 없으면 '가격반응 중심, 촉매 확인 필요'라고 쓴다.\n"
        "4. 사실과 해석을 구분한다.\n"
        "5. 한국장 영향은 SOX, EWY/KORU, USD/KRW, 한국 수급, 미국 관련 업종 중 실제 입력 근거가 있을 때만 판단한다.\n"
        "6. sector_baskets에서 여러 종목이 함께 움직이면 '섹터 확산', 한 종목만 움직이면 '개별주'라고 구분한다.\n"
        "7. CPI/PPI/FOMC/고용 등 일정은 news_issues에 일정·시각이 실제로 있는 경우에만 적는다.\n"
        "8. 가상자산, 매수·매도 지시, 목표가·손절가 표현은 금지한다.\n"
        "9. Telegram에서 읽기 쉽게 짧은 문장과 숫자 중심으로 작성한다.\n"
        "10. 반드시 JSON 객체 하나만 반환한다. 키는 report, audit 두 개다.\n"
        "audit는 {\"pass\":true|false,\"score\":0~100,\"reason\":\"...\"}. 85점 미만이면 pass=false.\n\n"
        "report 형식:\n"
        "🇺🇸 MM/DD 미국증시 마감 → 🇰🇷 한국장 프리뷰\n"
        "🔥 오늘의 한줄: [핵심 촉매] → [미국시장 반응] → [한국장 영향]\n\n"
        "📊 주요 지수\n"
        "- 다우 / S&P500 / 나스닥 / 러셀2000 / SOX: 값과 등락률\n\n"
        "🌡️ 주요 시장 지표\n"
        "- DXY / 미국10년물 / VIX / WTI / USDKRW\n"
        "- 시장 레짐: RISK-ON | NEUTRAL | RISK-OFF\n\n"
        "🧭 시장이 움직인 핵심 원인\n"
        "1) 사실 / 시장반응 / 의미\n"
        "2) 필요시 추가\n\n"
        "🚀 주도 섹터\n"
        "- AI인프라 / 반도체·메모리 / 광통신·네트워크 / 전력·데이터센터 / 양자컴퓨팅 / 원자력 / 배터리·리튬 / 우주 중 실제 확인된 것만\n"
        "- 대표 종목 등락률, 촉매, 확산도\n\n"
        "🇰🇷 한국장 영향\n"
        "- EWY / KORU / USDKRW / 외국인·기관 수급\n"
        "- 유리한 섹터 / 부담 섹터\n"
        "- 판정: 강세 우위 | 선별 강세 | 중립 | 방어 우위 + 이유\n\n"
        "⚠️ 오늘의 리스크\n"
        "- 선반영, 갭상승 차익실현, 금리·달러 반전, 이벤트 변동성 등 입력 근거가 있는 것만\n\n"
        "🗓 주요 일정\n"
        "- 수집 뉴스에서 확인되는 일정만 KST로 표시. 없으면 '확인 가능한 일정 데이터 없음'\n\n"
        "✅ 3줄 요약\n"
        "1. 거시환경과 레짐\n2. 가장 강한 섹터와 이유\n3. 한국장 핵심 체크포인트\n\n"
        f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _extract_json(text: str) -> dict | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
                return data if isinstance(data, dict) else None
            except Exception:
                return None
    return None


def _audit(report: str) -> tuple[bool, str]:
    if not report.strip():
        return False, "empty"
    lowered = report.lower()
    if any(word.lower() in lowered for word in FORBIDDEN):
        return False, "forbidden_content"
    for required in ["미국증시", "주요 지수", "주도 섹터", "한국장 영향", "3줄 요약"]:
        if required not in report:
            return False, f"missing_section:{required}"
    return True, "pass"


def _gemini(payload: dict) -> tuple[str | None, str]:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None, "gemini_api_key_missing"
    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": _prompt(payload)}]}],
                "generationConfig": {
                    "temperature": 0.08,
                    "maxOutputTokens": 3500,
                    "responseMimeType": "application/json",
                },
            },
            timeout=45,
        )
        response.raise_for_status()
        raw = "".join(
            part.get("text", "")
            for part in response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        )
        parsed = _extract_json(raw)
        if not parsed:
            return None, "gemini_json_parse_failed"
        audit = parsed.get("audit") or {}
        if audit.get("pass") is False or int(audit.get("score") or 0) < 85:
            return None, f"gemini_self_audit_failed:{audit.get('reason') or 'unknown'}"
        report = str(parsed.get("report") or "").strip()
        ok, reason = _audit(report)
        if not ok:
            return None, reason
        return report[:MAX_REPORT_CHARS], f"Gemini({model}) audit={audit.get('score')}"
    except Exception as exc:
        return None, f"gemini_request_failed:{type(exc).__name__}"


def _fmt(item: dict) -> str:
    label = str(item.get("label") or item.get("ticker") or "?")
    price = item.get("price")
    change = item.get("change_pct")
    if not isinstance(price, (int, float)):
        return f"{label} 확인불가"
    price_text = f"{price:,.2f}" if abs(price) < 1000 else f"{price:,.0f}"
    change_text = f" {change:+.2f}%" if isinstance(change, (int, float)) else ""
    return f"{label} {price_text}{change_text}"


def _local(payload: dict, clusters: list, rule: str) -> str:
    now = datetime.fromisoformat(payload["generated_at_iso"])
    market = payload["market"]
    global_quotes = market.get("global_market_quotes") or []
    proxies = market.get("korea_proxies") or []
    baskets = market.get("sector_baskets") or {}
    regime = market.get("risk_regime") or {}
    lines = [
        f"🇺🇸 {now:%m/%d} 미국증시 마감 → 🇰🇷 한국장 프리뷰",
        "━━━━━━━━━━━━━━",
        f"🔥 오늘의 한줄: 상세 인과분석 확인불가 · 검증 시세 기준 시장 레짐 {regime.get('regime', '확인불가')}",
        "",
        "📊 주요 지수",
    ]
    for label in ["DOW", "S&P500", "NASDAQ", "RUSSELL2000", "SOX"]:
        lines.append("- " + _fmt(next((x for x in global_quotes if x.get("label") == label), {"label": label})))
    lines.extend(["", "🌡️ 주요 시장 지표"])
    for label in ["DXY", "US10Y", "VIX", "WTI"]:
        lines.append("- " + _fmt(next((x for x in global_quotes if x.get("label") == label), {"label": label})))
    usd = market.get("usd_krw")
    lines.append(f"- USD/KRW {usd:,.1f}" if isinstance(usd, (int, float)) else "- USD/KRW 확인불가")
    lines.append(f"- 시장 레짐: {regime.get('regime', '확인불가')}")
    lines.extend(["", "🧭 시장이 움직인 핵심 원인"])
    if clusters:
        for idx, cluster in enumerate(clusters[:5], 1):
            lines.append(f"{idx}) [{materiality_score(cluster)}/{materiality_grade(cluster)}] {display._display_title(cluster, 90)}")
    else:
        lines.append("- 중요도 게이트 통과 뉴스 없음")
    lines.extend(["", "🚀 주도 섹터"])
    shown = False
    for sector, members in baskets.items():
        valid = [m for m in members if isinstance(m.get("change_pct"), (int, float))]
        if valid:
            shown = True
            lines.append(f"- {sector}: " + ", ".join(_fmt(m) for m in valid[:3]))
    if not shown:
        lines.append("- 섹터 시세 확인불가")
    lines.extend(["", "🇰🇷 한국장 영향"])
    lines.append("- " + " / ".join(_fmt(x) for x in proxies) if proxies else "- EWY/KORU 확인불가")
    lines.append(f"- {market.get('supply_demand_line') or '투자자별 수급 확인불가'}")
    lines.append(f"- 판정 참고: {market.get('market_bias') or '시장 판단 미확인'}")
    lines.extend([
        "",
        "⚠️ 오늘의 리스크",
        "- Gemini 인과분석 실패 시 임의 해석하지 않음. 검증된 시세와 뉴스만 표시.",
        "",
        "🗓 주요 일정",
        "- 확인 가능한 일정 데이터 없음",
        "",
        "✅ 3줄 요약",
        f"1. 거시환경: {regime.get('regime', '확인불가')}",
        f"2. 섹터 확산: {'확인' if shown else '확인불가'}",
        f"3. 한국장: {market.get('market_bias') or '판단 데이터 부족'}",
        "",
        f"검증: 로컬 fallback · {rule}",
    ])
    return "\n".join(lines)[:MAX_REPORT_CHARS]


def build_us_close_dashboard(
    summaries: list[SummaryItem],
    hours: int = 12,
    timezone_name: str = "Asia/Seoul",
) -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    selected, stock_count, blocked, rule, pre_gate_count = strict._select_strict(summaries)
    clusters = display._drop_noise(selected)[:20]
    market = get_market_dashboard_context()
    payload = {
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M KST"),
        "generated_at_iso": now.isoformat(),
        "lookback_hours": hours,
        "market": market,
        "news_issues": _issue_payload(clusters, now),
        "quality": {
            "source_count": len(summaries),
            "stock_candidate_count": stock_count,
            "excluded_count": blocked,
            "pre_gate_count": pre_gate_count,
            "selected_count": len(clusters),
            "rule": rule,
        },
    }
    report, engine = _gemini(payload)
    if report:
        return report + f"\n\n검증: {engine} · 입력 수치 외 생성 금지"
    local = _local(payload, clusters, rule)
    if os.getenv("DEBUG_QUALITY", "0") == "1":
        local += f"\nGemini진단: {engine}"
    return local
