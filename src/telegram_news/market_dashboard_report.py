from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import os
import re
from typing import Any

import requests

from . import market_dashboard_data as dashboard_data
from . import strict_report as strict
from . import strict_report_v2 as display
from .morning_brief_config import GLOBAL_QUOTE_TICKERS, KOREA_PROXY_TICKERS, THEME_BASKETS
from .strict_quality import materiality_grade, materiality_score
from .summarizer import SummaryItem
from .symbol_resolver import resolve_symbols


# Kept as a module-level alias because existing tests and a few library callers
# monkeypatch this symbol directly. Production prefers the active atomic context.
get_market_dashboard_context = dashboard_data.get_market_dashboard_context

DEFAULT_MODEL = "gemini-2.5-flash"
MAX_REPORT_CHARS = int(os.getenv("US_CLOSE_MAX_REPORT_CHARS", "14000"))
GROUNDING_TIMEOUT = int(os.getenv("US_CLOSE_GROUNDING_TIMEOUT", "45"))
SECTION_SEPARATOR = "──────────"
PRIMARY_SOURCE_GRADES = {"A", "B"}
FORBIDDEN = [
    "비트코인",
    "이더리움",
    "업비트",
    "바이낸스",
    "손절가",
    "진입가",
    "무조건 매수",
    "확정 상승",
    "매수 추천",
]
INTERNAL_MARKERS = ["Gemini진단", "Google grounding", "primary_catalyst_eligible", "source_grade"]


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _explicit_symbols(cluster) -> list:
    """Return only securities explicitly recoverable from the news text."""
    try:
        symbols = list(display._display_symbols(cluster))
    except Exception:
        symbols = []
    if symbols:
        return symbols[:4]

    try:
        best = cluster.best()
        item = best.item
        text = f"{getattr(item, 'title', '')} {getattr(item, 'body', '')}"
        categories = list(getattr(item, "categories", None) or [])
        raw_tickers = list(getattr(item, "tickers", None) or [])
        resolved = resolve_symbols(text, categories=categories, raw_tickers=raw_tickers)
        return [symbol for symbol in resolved if getattr(symbol, "asset_type", "") != "crypto"][:4]
    except Exception:
        return []


def _source_grade(cluster) -> str:
    try:
        return str(materiality_grade(cluster) or "D").upper()
    except Exception:
        return "D"


def _primary_catalyst_eligible(cluster) -> bool:
    return _source_grade(cluster) in PRIMARY_SOURCE_GRADES


def _issue_payload(clusters: list, now: datetime) -> list[dict]:
    rows = []
    for idx, cluster in enumerate(clusters[:24], 1):
        best = cluster.best()
        symbols = _explicit_symbols(cluster)
        grade = _source_grade(cluster)
        rows.append(
            {
                "rank": idx,
                "importance": materiality_score(cluster),
                "source_grade": grade,
                "primary_catalyst_eligible": grade in PRIMARY_SOURCE_GRADES,
                "type": best.news_type,
                "title": display._display_title(cluster, 150),
                "body": strict.base._short(best.item.body or "", 620),
                "sectors": cluster.sectors()[:6],
                "symbols": [{"name": str(s.name or ""), "ticker": s.ticker} for s in symbols],
                "age": display._age_line(cluster, now),
                "source_url": display._source_url(cluster),
            }
        )
    return rows


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


def _grounding_sources(candidate: dict) -> list[dict]:
    metadata = candidate.get("groundingMetadata") or {}
    sources = []
    seen = set()
    for chunk in metadata.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        uri = str(web.get("uri") or "").strip()
        title = str(web.get("title") or "").strip()
        if not uri or uri in seen:
            continue
        seen.add(uri)
        sources.append({"title": title, "uri": uri})
    return sources[:20]


def _normalization_prompt(raw: str, now: datetime) -> str:
    return f"""
Convert the grounded research memo below into ONE JSON object. Do not add facts that are absent from the memo. If a value is not explicitly verified, use null or an empty list. Do not infer dates, consensus values, market reactions, analyst targets, or causal links. Keep separate facts separate. Times should remain in KST when present.

Required schema:
{{
  "session_headline": "",
  "summary_points": [""],
  "macro_releases": [
    {{
      "name":"", "released_at_kst":"", "actual":null, "consensus":null, "previous":null,
      "unit":"", "surprise":"higher|lower|inline|unknown", "details":[""], "market_relevance":""
    }}
  ],
  "macro_topics": [
    {{"title":"", "facts":[""], "market_relevance":"", "affected_assets":[""]}}
  ],
  "positioning": [
    {{"title":"", "fact":"", "market_relevance":""}}
  ],
  "analyst_actions": [
    {{
      "company":"", "ticker":"", "firm":"", "action":"", "rating":"",
      "target_price":null, "currency":"", "fact":"", "market_relevance":""
    }}
  ],
  "company_catalysts": [
    {{"company":"", "ticker":"", "fact":"", "market_reaction":"", "peer_readthrough":""}}
  ],
  "upcoming_events": [
    {{"name":"", "scheduled_at_kst":"", "consensus":null, "previous":null, "unit":"", "why_it_matters":""}}
  ],
  "earnings_and_guidance": [
    {{"company":"", "ticker":"", "event_time_kst":"", "fact":"", "market_relevance":""}}
  ],
  "market_catalysts": [
    {{"fact":"", "observed_market_reaction":"", "korea_link":""}}
  ]
}}

Seoul timestamp: {now:%Y-%m-%d %H:%M KST}
GROUNDED_MEMO:
{raw[:18000]}
""".strip()


def _normalize_grounded_research(raw: str, *, api_key: str, model: str, now: datetime) -> tuple[dict | None, str]:
    parsed = _extract_json(raw)
    if parsed:
        return parsed, "direct_json"
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": _normalization_prompt(raw, now)}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "maxOutputTokens": 4400,
                    "responseMimeType": "application/json",
                },
            },
            timeout=GROUNDING_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        text = "".join(
            part.get("text", "")
            for part in (body.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        )
        return _extract_json(text), "normalized_json"
    except Exception as exc:
        return None, f"normalization_request_failed:{type(exc).__name__}"


def _grounded_market_research(now: datetime) -> tuple[dict, str]:
    """Research the latest US close with enough depth for a desk-quality brief."""
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return {}, "grounding_api_key_missing"
    if os.getenv("US_CLOSE_GOOGLE_GROUNDING", "1") == "0":
        return {}, "grounding_disabled"

    model = os.getenv("GEMINI_GROUNDING_MODEL", os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    date_text = now.strftime("%Y-%m-%d")
    prompt = f"""
You are the fact-checking research desk for a Korean institutional-style US-close morning briefing.
Current Seoul date/time: {now:%Y-%m-%d %H:%M KST}.
Use Google Search for CURRENT, verifiable information only. The final writer needs enough detail to produce a briefing comparable to a professional sell-side morning note, not a headline list.

Research window:
- The latest completed US regular session through now, plus the preceding 36 hours when needed to explain the move.
- High-impact US economic releases and their internal components, not just the headline number.
- Major scheduled events for the next 36 hours only when verified.

Research these buckets when material:
1. MARKET DRIVER: what actually moved US equities in the latest session; distinguish the trigger from the price reaction.
2. RATES / FISCAL: US 10Y and 30Y yield context, Treasury supply/auctions, fiscal-deficit concerns, corporate bond issuance, foreign demand, and whether the move is part of a global long-end selloff.
3. GEOPOLITICS / ENERGY: Middle East or other supply risks, shipping incidents, OPEC-related developments, and the inflation transmission channel. Do not promote rumors.
4. GLOBAL CENTRAL BANKS: BOJ/ECB/other major policy or long-yield changes that can spill into US duration or carry trades.
5. POSITIONING: major fund-manager surveys, hedge-fund positioning, options positioning, insider buying/selling, or well-known investor warnings only when a reputable source provides a concrete statistic or attributed statement.
6. ECONOMIC DATA: actual vs consensus vs previous, plus important subcomponents such as manufacturing, autos, housing, semiconductors, computers, services, or labor details when the release contains them.
7. SECTOR ROTATION: semiconductors/memory, storage, AI infrastructure/neocloud, optical networking, software, healthcare/defensives, mega-cap tech, and data-center power. Identify whether moves were broad sector moves or isolated names.
8. COMPANY CATALYSTS: material company-specific news for the largest movers in those sectors, including litigation/regulation, financing, capex, customer contracts, data-center projects, earnings/guidance, or product news.
9. SELL-SIDE ACTIONS: brokerage rating/target changes only when firm, company, rating/action, and target are explicitly verified. These are analyst targets, not trading instructions.

Rules:
- Never invent a number, quote, target price, survey statistic, causal link, or event time.
- Prefer primary/official sources (BLS, BEA, Federal Reserve, Treasury, company IR) for facts and major financial media for market reaction.
- If reporting 'profit taking', 'no new fundamental bad news', or 'rotation', require reputable commentary or broad peer-price evidence; do not infer it from one stock falling.
- Separate the observed market reaction from the explanation.
- Convert event times to Asia/Seoul only when the source time is known.
- Do not include crypto.
- Do not give our own buy/sell/entry/stop/target advice.
- Keep only items that materially help explain {date_text}'s morning brief.
- Produce a detailed fact memo with concrete names, numbers, peer moves, and attribution where verified.
""".strip()

    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 5200},
            },
            timeout=GROUNDING_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        candidate = (body.get("candidates") or [{}])[0]
        raw = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", []))
        if not raw.strip():
            return {}, "grounding_empty_response"
        parsed, normalize_engine = _normalize_grounded_research(raw, api_key=api_key, model=model, now=now)
        if not parsed:
            return {}, f"grounding_json_normalize_failed:{normalize_engine}"
        parsed["sources"] = _grounding_sources(candidate)
        metadata = candidate.get("groundingMetadata") or {}
        parsed["search_queries"] = (metadata.get("webSearchQueries") or [])[:16]
        return parsed, f"google_search_grounding:{model}:{normalize_engine}"
    except Exception as exc:
        return {}, f"grounding_request_failed:{type(exc).__name__}"


def _prompt(payload: dict) -> str:
    return (
        "너는 한국 기관투자자용 미국장 마감 모닝노트를 작성하는 글로벌 매크로·주식 리서치 데스크다.\n"
        "목표는 단순 지수 나열이 아니라 '무슨 일이 있었고 → 왜 자산이 그렇게 움직였고 → 어느 업종으로 자금이 이동했는지'를 구체적 수치와 종목으로 설명하는 것이다.\n\n"
        "사실성 절대 규칙:\n"
        "1. INPUT_JSON에 있는 사실과 숫자만 사용한다. 입력에 없는 수치·원인·인용·목표가·일정을 만들지 않는다.\n"
        "2. 시장 가격/등락률은 market의 동일 snapshot 수치를 최우선으로 사용한다. 뉴스나 grounded_research에 다른 가격이 있어도 market 수치와 섞지 않는다.\n"
        "3. 값이 없거나 충돌하면 '확인불가'라고 쓰거나 해당 문장을 생략한다.\n"
        "4. 급락 자체를 원인으로 설명하지 않는다. 금리, 수급, 실적, 규제, 지정학 등 검증된 촉매가 있어야 인과문장을 쓴다.\n"
        "5. '차익실현', '펀더멘털 훼손 없음', '순환매' 같은 해석은 broad peer move 또는 grounded_research의 검증된 시장 코멘트가 있을 때만 쓴다.\n"
        "6. news_issues의 C/D 출처는 단독으로 핵심 원인에 올리지 않는다. A/B 또는 grounded_research로 검증된 경우에만 중요 서사에 사용한다.\n"
        "7. 증권사의 '매수 의견/목표가'는 grounded_research.analyst_actions에 회사·증권사·행동·목표가가 명시된 경우에만 쓸 수 있다. 이는 증권사 견해로 명확히 귀속하고 우리 자체 목표가처럼 쓰지 않는다.\n"
        "8. 가상자산과 우리 자체 매수·매도·진입·손절·목표가 지시는 금지한다.\n"
        "9. 비슷한 말을 반복하지 말고, 한 문장마다 새로운 사실·인과·비교를 담는다.\n"
        "10. 문체는 사용자가 제공한 전문 모닝 시황 예시처럼 간결한 서술형 한국어로 쓴다. 이모지는 쓰지 않는다.\n\n"
        "내용 품질 규칙:\n"
        "- 첫 제목은 그날 시장을 한 문장으로 요약한다. 예: '[금리/지정학/실적]에 [섹터] 약세, [대체 섹터]로 순환매'. 단 실제 입력 근거가 있어야 한다.\n"
        "- <요약>은 5~7줄. 지수 방향, 핵심 거시 촉매, 금리/유가, 가장 큰 약세 섹터, 강한 섹터, 한국장 연결을 압축한다. 각 줄 앞에 불릿 기호를 붙이지 않는다.\n"
        "- <주요 지수 종합>은 다우·S&P500·Nasdaq를 반드시 포함하고, Russell2000·SOX·EWY/KORU/SOXX·미10년물·미30년물·원/달러 중 입력에 있는 값을 추가한다. 값과 일간 등락률을 같은 줄에 쓴다.\n"
        "- <경제지표>는 실제/예상/이전뿐 아니라 중요한 세부항목과 시장 해석을 2~4문장으로 설명한다. 발표가 없으면 '검증된 주요 경제지표 발표 없음' 한 줄만 쓴다.\n"
        "- <매크로>는 3~5개 핵심 주제를 번호로 정리하고 각 주제당 2~4문장. 장기금리, 재정/채권공급, 지정학/유가, 글로벌 중앙은행, 포지셔닝 중 실제로 중요한 것만 쓴다.\n"
        "- <원자재>는 금·은과 WTI·브렌트를 가능한 범위에서 표시한다. '최근 미국 현지 정산가/일봉 snapshot 기준, 이후 실시간 변동 가능'이라고 명시한다.\n"
        "- <주요 테마>는 실제 가격 변동이 큰 테마와 뉴스가 있는 테마 3~6개만 고른다. '[반도체·메모리]'처럼 테마 제목을 쓰고, 종목별로 '1. 종목명 (+/-x.xx%)' 다음 2~4문장으로 촉매·동종업계 동반 움직임·검증된 증권사 의견을 설명한다.\n"
        "- 가격은 움직였지만 촉매를 검증하지 못했으면 '신규 촉매 확인불가'라고 명시하고 임의 이유를 붙이지 않는다.\n"
        "- theme_baskets의 여러 종목이 같은 방향이면 섹터 확산으로 설명하고, 한 종목만 움직이면 개별주 이슈로 구분한다.\n\n"
        "반드시 아래 형식을 그대로 지킨다. 헤더 이름을 바꾸거나 새로운 대분류를 추가하지 않는다.\n\n"
        "[YYYY년 M월 D일 모닝 시황]\n"
        "그날 시장의 핵심을 요약한 한 줄 제목\n\n"
        "──────────\n"
        "<요약>\n"
        "요약문 1\n"
        "요약문 2\n"
        "...\n\n"
        "──────────\n"
        "<주요 지수 종합>\n"
        "다우존스: 값 (등락률)\n"
        "S&P 500: 값 (등락률)\n"
        "Nasdaq: 값 (등락률)\n"
        "기타 검증된 선행지표/금리/환율\n\n"
        "──────────\n"
        "<경제지표>\n"
        "1. (지표명 / 핵심 수치)\n"
        "상세 설명\n\n"
        "──────────\n"
        "<매크로>\n"
        "1. 핵심 주제\n"
        "상세 설명\n\n"
        "──────────\n"
        "<원자재>\n"
        "(최근 미국 현지 정산가/일봉 snapshot 기준, 이후 실시간 변동 가능)\n"
        "1. 금, 은 가격\n"
        "금 ... , 은 ...\n\n"
        "2. 유가\n"
        "WTI ... , 브렌트유 ...\n\n"
        "──────────\n"
        "<주요 테마>\n"
        "[테마명]\n"
        "1. 종목명 (+/-x.xx%)\n"
        "2~4문장 설명\n\n"
        "반드시 JSON 객체 하나만 반환한다. 키는 report, audit 두 개다.\n"
        "audit는 {\"pass\":true|false,\"score\":0~100,\"reason\":\"...\"}. 형식 누락, 숫자 혼입, 원인 날조, C/D 단독 핵심원인, 자체 매매지시가 있으면 pass=false. 90점 미만도 pass=false.\n\n"
        f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _audit(report: str) -> tuple[bool, str]:
    if not report.strip():
        return False, "empty"
    lowered = report.lower()
    if any(word.lower() in lowered for word in FORBIDDEN):
        return False, "forbidden_content"
    if any(marker.lower() in lowered for marker in INTERNAL_MARKERS):
        return False, "internal_diagnostic_leak"
    if not re.search(r"^\[\d{4}년\s+\d{1,2}월\s+\d{1,2}일\s+모닝\s+시황\]", report.strip()):
        return False, "bad_header"
    required = ["<요약>", "<주요 지수 종합>", "<경제지표>", "<매크로>", "<원자재>", "<주요 테마>"]
    for section in required:
        if section not in report:
            return False, f"missing_section:{section}"
    if report.count(SECTION_SEPARATOR) < 6:
        return False, "missing_separators"
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
                    "maxOutputTokens": 6200,
                    "responseMimeType": "application/json",
                },
            },
            timeout=60,
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
        if audit.get("pass") is False or int(audit.get("score") or 0) < 90:
            return None, f"gemini_self_audit_failed:{audit.get('reason') or 'unknown'}"
        report = str(parsed.get("report") or "").strip()
        ok, reason = _audit(report)
        if not ok:
            return None, reason
        return report[:MAX_REPORT_CHARS], f"Gemini({model}) audit={audit.get('score')}"
    except Exception as exc:
        return None, f"gemini_request_failed:{type(exc).__name__}"


def _quote_from_assets(assets: dict[str, dict], label: str, ticker: str) -> dict:
    item = dict(assets.get(ticker) or {})
    item["label"] = label
    item["ticker"] = ticker
    return item


def _active_atomic_market_context() -> dict | None:
    """Build the dashboard exclusively from the active one-shot MarketSnapshot."""
    try:
        from .report_integrity import current_context

        context = current_context()
    except Exception:
        context = None
    if context is None:
        return None

    global_snapshot = context.market.global_snapshot
    assets = dict(global_snapshot.get("assets") or {})
    base_context = context.market.market_context
    global_quotes = [
        _quote_from_assets(assets, label, ticker)
        for label, ticker in GLOBAL_QUOTE_TICKERS.items()
    ]
    korea_proxies = [
        _quote_from_assets(assets, label, ticker)
        for label, ticker in KOREA_PROXY_TICKERS.items()
    ]
    sector_baskets: dict[str, list[dict]] = {}
    for sector, members in THEME_BASKETS.items():
        rows = [_quote_from_assets(assets, name, ticker) for name, ticker in members.items()]
        rows.sort(
            key=lambda row: (
                isinstance(row.get("change_pct"), (int, float)),
                abs(float(row.get("change_pct") or 0.0)),
            ),
            reverse=True,
        )
        sector_baskets[sector] = rows

    sp500 = assets.get(GLOBAL_QUOTE_TICKERS["S&P500"]) or {}
    return {
        "global_market_quotes": global_quotes,
        "us_session_date": sp500.get("session_date"),
        "risk_regime": dashboard_data._risk_regime(global_quotes),
        "korea_proxies": korea_proxies,
        "sector_baskets": sector_baskets,
        "usd_krw": base_context.get("usd_krw"),
        "kospi_change_pct": base_context.get("kospi_change_pct"),
        "kosdaq_change_pct": base_context.get("kosdaq_change_pct"),
        "sp500_change_pct": context.market.index_values.get("sp500_change_pct"),
        "nasdaq_change_pct": context.market.index_values.get("nasdaq_change_pct"),
        "investor_flow": base_context.get("investor_flow") or [],
        "supply_demand_line": base_context.get("supply_demand_line") or "투자자별 수급 확인불가",
        "market_bias": base_context.get("market_bias") or "시장 판단 미확인",
        "top_sectors_by_volume": base_context.get("top_sectors_by_volume") or [],
        "market_cap_leaders": base_context.get("market_cap_leaders") or [],
        "source": "atomic ReportRunContext / Yahoo Finance daily bars + existing KR market context",
        "timestamp": context.market.captured_at.isoformat(timespec="seconds"),
        "snapshot_atomic": True,
    }


def _fmt(item: dict, *, label_override: str | None = None) -> str:
    raw_label = str(item.get("label") or item.get("ticker") or "?")
    label = label_override or raw_label
    price = _safe_float(item.get("price"))
    change = _safe_float(item.get("change_pct"))
    if price is None:
        return f"{label}: 확인불가"
    if raw_label in {"US10Y", "US30Y"}:
        price_text = f"{price:.4f}%"
    elif raw_label in {"DOW", "S&P500", "NASDAQ", "RUSSELL2000", "SOX"}:
        price_text = f"{price:,.2f}"
    elif raw_label in {"GOLD", "SILVER", "WTI", "BRENT"}:
        price_text = f"${price:,.3f}" if price < 100 else f"${price:,.2f}"
    else:
        price_text = f"{price:,.2f}" if abs(price) < 10000 else f"{price:,.0f}"
    change_text = f" ({change:+.2f}%)" if change is not None else ""
    return f"{label}: {price_text}{change_text}"


def _find_quote(market: dict, label: str) -> dict:
    return next((row for row in (market.get("global_market_quotes") or []) if row.get("label") == label), {"label": label})


def _find_proxy(market: dict, label: str) -> dict:
    return next((row for row in (market.get("korea_proxies") or []) if row.get("label") == label), {"label": label})


def _macro_value(value: Any, unit: str = "") -> str:
    if value is None or value == "":
        return "확인불가"
    return f"{value}{unit or ''}"


def _fallback_headline(research: dict, market: dict) -> str:
    explicit = str(research.get("session_headline") or "").strip()
    if explicit:
        return explicit[:140]
    catalysts = research.get("market_catalysts") or []
    if catalysts:
        fact = str(catalysts[0].get("fact") or "").strip()
        reaction = str(catalysts[0].get("observed_market_reaction") or "").strip()
        text = " · ".join(part for part in (fact, reaction) if part)
        if text:
            return text[:140]
    spx = _safe_float(_find_quote(market, "S&P500").get("change_pct"))
    sox = _safe_float(_find_quote(market, "SOX").get("change_pct"))
    if spx is not None and sox is not None:
        return f"S&P500 {spx:+.2f}%, 반도체지수 {sox:+.2f}% — 검증된 핵심 촉매는 추가 확인 필요"
    return "미국장 마감 흐름 확인 — 검증된 핵심 촉매는 추가 확인 필요"


def _summary_fallback(research: dict, market: dict) -> list[str]:
    points = [str(value).strip() for value in (research.get("summary_points") or []) if str(value).strip()]
    if points:
        return points[:7]

    lines = []
    equity_parts = []
    for label in ["DOW", "S&P500", "NASDAQ", "SOX"]:
        change = _safe_float(_find_quote(market, label).get("change_pct"))
        if change is not None:
            equity_parts.append(f"{label} {change:+.2f}%")
    if equity_parts:
        lines.append("미국 주요 지수: " + " / ".join(equity_parts))

    rate_parts = []
    for label in ["US10Y", "US30Y"]:
        quote = _find_quote(market, label)
        price = _safe_float(quote.get("price"))
        if price is not None:
            rate_parts.append(f"{label} {price:.4f}%")
    if rate_parts:
        lines.append("장기금리: " + " / ".join(rate_parts))

    oil = _find_quote(market, "WTI")
    oil_price = _safe_float(oil.get("price"))
    if oil_price is not None:
        oil_change = _safe_float(oil.get("change_pct"))
        lines.append(f"WTI ${oil_price:,.2f}" + (f" ({oil_change:+.2f}%)" if oil_change is not None else ""))

    baskets = market.get("sector_baskets") or {}
    ranked: list[tuple[float, str, float]] = []
    for sector, members in baskets.items():
        changes = [_safe_float(row.get("change_pct")) for row in members]
        valid = [value for value in changes if value is not None]
        if valid:
            ranked.append((abs(sum(valid) / len(valid)), sector, sum(valid) / len(valid)))
    ranked.sort(reverse=True)
    if ranked:
        _, sector, mean = ranked[0]
        lines.append(f"가장 큰 섹터 변동: {sector} 평균 {mean:+.2f}% — 세부 촉매는 아래 테마에서 확인")

    proxy_parts = []
    for label in ["EWY", "KORU", "SOXX"]:
        change = _safe_float(_find_proxy(market, label).get("change_pct"))
        if change is not None:
            proxy_parts.append(f"{label} {change:+.2f}%")
    if proxy_parts:
        lines.append("한국장 선행 프록시: " + " / ".join(proxy_parts))

    return lines[:7] or ["검증된 요약 데이터가 충분하지 않아 지수·뉴스 재확인 필요"]


def _primary_clusters(clusters: list) -> list:
    return [cluster for cluster in clusters if _primary_catalyst_eligible(cluster)]


def _company_context(research: dict, ticker: str) -> list[str]:
    lines: list[str] = []
    for item in research.get("company_catalysts") or []:
        if str(item.get("ticker") or "").upper() != ticker.upper():
            continue
        fact = str(item.get("fact") or "").strip()
        reaction = str(item.get("market_reaction") or "").strip()
        peer = str(item.get("peer_readthrough") or "").strip()
        if fact:
            lines.append(fact)
        if reaction:
            lines.append(reaction)
        if peer:
            lines.append(peer)
    for item in research.get("analyst_actions") or []:
        if str(item.get("ticker") or "").upper() != ticker.upper():
            continue
        firm = str(item.get("firm") or "증권사").strip()
        action = str(item.get("action") or item.get("rating") or "").strip()
        target = item.get("target_price")
        currency = str(item.get("currency") or "$").strip()
        fact = str(item.get("fact") or "").strip()
        parts = [firm]
        if action:
            parts.append(action)
        if target is not None:
            parts.append(f"목표가 {currency}{target}")
        if fact:
            parts.append(fact)
        lines.append(" · ".join(parts))
    return lines[:3]


def _session_freshness(market: dict, now: datetime) -> tuple[bool | None, str, str]:
    """Validate that a Tue-Sat KST morning brief represents the prior US date."""
    actual = str(market.get("us_session_date") or "").strip()
    expected = (now.date() - timedelta(days=1)).isoformat()
    if now.weekday() not in {1, 2, 3, 4, 5}:
        return None, expected, actual
    if not actual:
        return None, expected, actual
    return actual == expected, expected, actual


def _stale_session_notice(now: datetime, expected: str, actual: str) -> str:
    observed = actual or "확인불가"
    return "\n".join(
        [
            f"[{now.year}년 {now.month}월 {now.day}일 모닝 시황]",
            "미국 정규장 휴장·시장 데이터 미갱신",
            "",
            SECTION_SEPARATOR,
            "<요약>",
            f"이번 브리핑이 요구하는 미국 거래일은 {expected}이지만 최근 확인 세션은 {observed}",
            "이전 거래일 마감 데이터를 오늘 마감처럼 재전송하지 않음",
            "뉴스 수집과 전략 학습은 별도 정기 작업에서 계속 진행",
        ]
    )


def _local(payload: dict, clusters: list, rule: str) -> str:
    now = datetime.fromisoformat(payload["generated_at_iso"])
    market = payload["market"]
    research = payload.get("grounded_research") or {}
    lines = [
        f"[{now.year}년 {now.month}월 {now.day}일 모닝 시황]",
        _fallback_headline(research, market),
        "",
        SECTION_SEPARATOR,
        "<요약>",
    ]
    lines.extend(_summary_fallback(research, market))

    lines.extend(["", SECTION_SEPARATOR, "<주요 지수 종합>"])
    label_names = {
        "DOW": "다우존스",
        "S&P500": "S&P 500",
        "NASDAQ": "Nasdaq",
        "RUSSELL2000": "Russell 2000",
        "SOX": "필라델피아 반도체",
    }
    for label in ["DOW", "S&P500", "NASDAQ", "RUSSELL2000", "SOX"]:
        lines.append(_fmt(_find_quote(market, label), label_override=label_names[label]))
    for label in ["EWY", "KORU", "SOXX"]:
        item = _find_proxy(market, label)
        if _safe_float(item.get("price")) is not None:
            lines.append(_fmt(item))
    for label, name in [("US10Y", "미국 10년물"), ("US30Y", "미국 30년물")]:
        item = _find_quote(market, label)
        price = _safe_float(item.get("price"))
        change = _safe_float(item.get("change_pct"))
        if price is not None:
            lines.append(f"{name}: {price:.4f}%" + (f" ({change:+.2f}%)" if change is not None else ""))
    usd = _safe_float(market.get("usd_krw"))
    if usd is not None:
        lines.append(f"원/달러: {usd:,.2f}원")

    lines.extend(["", SECTION_SEPARATOR, "<경제지표>"])
    macro = research.get("macro_releases") or []
    if macro:
        for idx, item in enumerate(macro[:5], 1):
            unit = str(item.get("unit") or "")
            actual = _macro_value(item.get("actual"), unit)
            consensus = _macro_value(item.get("consensus"), unit)
            previous = _macro_value(item.get("previous"), unit)
            lines.append(f"{idx}. ({item.get('name') or '경제지표'} / 실제 {actual}, 예상 {consensus}, 이전 {previous})")
            for detail in item.get("details") or []:
                if str(detail).strip():
                    lines.append(str(detail).strip())
            relevance = str(item.get("market_relevance") or "").strip()
            if relevance:
                lines.append(relevance)
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()
    else:
        lines.append("검증된 주요 경제지표 발표 없음")

    lines.extend(["", SECTION_SEPARATOR, "<매크로>"])
    topics = research.get("macro_topics") or []
    catalysts = research.get("market_catalysts") or []
    if topics:
        for idx, item in enumerate(topics[:5], 1):
            lines.append(f"{idx}. {item.get('title') or '매크로 이슈'}")
            for fact in item.get("facts") or []:
                if str(fact).strip():
                    lines.append(str(fact).strip())
            relevance = str(item.get("market_relevance") or "").strip()
            if relevance:
                lines.append(relevance)
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()
    elif catalysts:
        for idx, item in enumerate(catalysts[:5], 1):
            lines.append(f"{idx}. {item.get('fact') or '매크로 이슈'}")
            reaction = str(item.get("observed_market_reaction") or "").strip()
            if reaction:
                lines.append(reaction)
            korea = str(item.get("korea_link") or "").strip()
            if korea:
                lines.append(korea)
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()
    else:
        primary = _primary_clusters(clusters)
        if primary:
            for idx, cluster in enumerate(primary[:4], 1):
                lines.append(f"{idx}. {display._display_title(cluster, 120)}")
                body = str(getattr(cluster.best().item, "body", "") or "").strip()
                if body:
                    lines.append(strict.base._short(body, 260))
        else:
            lines.append("출처 A/B로 검증된 핵심 촉매 없음")
            if clusters:
                weak = sorted(clusters, key=materiality_score, reverse=True)[:3]
                for cluster in weak:
                    lines.append(
                        f"보조 관찰 [중요도 {materiality_score(cluster)} · 출처 {_source_grade(cluster)}] "
                        f"{display._display_title(cluster, 100)}"
                    )

    lines.extend(["", SECTION_SEPARATOR, "<원자재>"])
    lines.append("(최근 미국 현지 정산가/일봉 snapshot 기준, 이후 실시간 변동 가능)")
    lines.append("1. 금, 은 가격")
    lines.append(f"{_fmt(_find_quote(market, 'GOLD'), label_override='금')}, {_fmt(_find_quote(market, 'SILVER'), label_override='은')}")
    lines.append("")
    lines.append("2. 유가")
    lines.append(f"{_fmt(_find_quote(market, 'WTI'), label_override='WTI')}, {_fmt(_find_quote(market, 'BRENT'), label_override='브렌트유')}")

    lines.extend(["", SECTION_SEPARATOR, "<주요 테마>"])
    baskets = market.get("sector_baskets") or {}
    ranked: list[tuple[float, str, list[dict]]] = []
    for sector, members in baskets.items():
        valid = [row for row in members if _safe_float(row.get("change_pct")) is not None]
        if not valid:
            continue
        magnitude = sum(abs(float(row["change_pct"])) for row in valid) / len(valid)
        ranked.append((magnitude, sector, valid))
    ranked.sort(key=lambda row: row[0], reverse=True)

    if not ranked:
        lines.append("검증 가능한 테마별 종목 시세 없음")
    else:
        for _, sector, members in ranked[:6]:
            lines.append(f"[{sector}]")
            for idx, row in enumerate(members[:4], 1):
                change = _safe_float(row.get("change_pct"))
                ticker = str(row.get("ticker") or "")
                lines.append(f"{idx}. {row.get('label') or ticker} ({change:+.2f}%)")
                context_lines = _company_context(research, ticker)
                if context_lines:
                    lines.extend(context_lines)
                else:
                    lines.append("신규 촉매 확인불가 — 가격 움직임만 확인, 임의 원인 부여 금지")
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()

    quality = payload.get("quality") or {}
    grounding_engine = str(quality.get("grounding_engine") or "")
    if not research and grounding_engine.startswith("grounding_"):
        lines.extend(["", f"Google grounding 미사용({grounding_engine})"])

    return "\n".join(lines)[:MAX_REPORT_CHARS]


def build_us_close_dashboard(
    summaries: list[SummaryItem],
    hours: int = 12,
    timezone_name: str = "Asia/Seoul",
) -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    selected, stock_count, blocked, rule, pre_gate_count = strict._select_strict(summaries)
    clusters = display._drop_noise(selected)[:24]

    # Production runs are already inside report_integrity.activate_context().
    # Reusing that snapshot prevents the old second Yahoo fetch from introducing
    # contradictory index values inside the same morning brief.
    market = _active_atomic_market_context()
    snapshot_source = "atomic" if market is not None else "standalone"
    if market is None:
        market = get_market_dashboard_context()

    fresh, expected_session, actual_session = _session_freshness(market, now)
    if fresh is False:
        print(
            "[market-dashboard] stale_session_blocked "
            f"expected={expected_session} actual={actual_session or 'missing'} source={snapshot_source}"
        )
        return _stale_session_notice(now, expected_session, actual_session)

    grounded_research, grounding_engine = _grounded_market_research(now)
    payload = {
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M KST"),
        "generated_at_iso": now.isoformat(),
        "lookback_hours": hours,
        "market": market,
        "grounded_research": grounded_research,
        "news_issues": _issue_payload(clusters, now),
        "quality": {
            "source_count": len(summaries),
            "stock_candidate_count": stock_count,
            "excluded_count": blocked,
            "pre_gate_count": pre_gate_count,
            "selected_count": len(clusters),
            "primary_catalyst_count": sum(_primary_catalyst_eligible(cluster) for cluster in clusters),
            "rule": rule,
            "grounding_engine": grounding_engine,
            "grounding_source_count": len(grounded_research.get("sources") or []),
            "expected_us_session_date": expected_session,
            "actual_us_session_date": actual_session,
            "market_snapshot_source": snapshot_source,
        },
    }
    report, engine = _gemini(payload)
    print(
        "[market-dashboard] "
        f"grounding={grounding_engine} sources={len(grounded_research.get('sources') or [])} "
        f"final={engine} selected={len(clusters)} primary={payload['quality']['primary_catalyst_count']} "
        f"session={actual_session or 'unknown'} snapshot={snapshot_source}"
    )
    if report:
        return report
    local = _local(payload, clusters, rule)
    if os.getenv("DEBUG_QUALITY", "0") == "1":
        local += f"\n\n[DEBUG] final={engine} / grounding={grounding_engine}"
    return local
