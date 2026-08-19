from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from hashlib import sha1, sha256
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from .global_market_tracker import fetch_asset_snapshot
from .normalizer import deduplicate_rows
from .notifier import send_telegram_message_to_many
from .settings import load_channels, load_settings
from .store import connect, fetch_recent, init_db, insert_messages
from .summarizer import SummaryItem, gemini_classify_if_available
from .telegram_client import collect_messages


KST = ZoneInfo("Asia/Seoul")
DEFAULT_MODEL = "gemini-2.5-flash"
STATE_PATH = Path("reports/market_insight_state.json")
LATEST_PATH = Path("reports/latest_market_insight.json")
ALERT_SCORE = int(os.getenv("INSIGHT_ALERT_SCORE", "82"))
COOLDOWN_HOURS = int(os.getenv("INSIGHT_COOLDOWN_HOURS", "12"))
MEMORY_HOURS = int(os.getenv("INSIGHT_MEMORY_HOURS", "168"))
REQUEST_TIMEOUT = int(os.getenv("INSIGHT_REQUEST_TIMEOUT", "45"))

# Cross-asset confirmation set. These are confirmation/rotation proxies, not recommendations.
INSIGHT_MARKET_ASSETS: dict[str, str] = {
    "SPY": "S&P500",
    "QQQ": "나스닥100",
    "SOXX": "반도체",
    "XBI": "나스닥바이오",
    "XLE": "에너지",
    "XLF": "금융",
    "XLV": "헬스케어",
    "XLU": "유틸리티",
    "XLP": "필수소비재",
    "IWM": "미국중소형",
    "EWY": "한국주식",
    "KORU": "한국3배",
    "TLT": "미국장기채",
    "GLD": "금",
    "HYG": "하이일드",
    "JEPI": "커버드콜",
    "QYLD": "나스닥커버드콜",
    "BTC-USD": "비트코인",
    "^KS11": "KOSPI",
    "^KQ11": "KOSDAQ",
}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return None if math.isnan(out) or math.isinf(out) else out
    except Exception:
        return None


def _clean_secret(value: str | None) -> str:
    return "".join(str(value or "").strip().strip('"\'').split())


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _source_groups(urls: list[str] | None, channels: list[str] | None = None) -> list[str]:
    groups: list[str] = []
    for url in urls or []:
        try:
            host = urlparse(str(url)).netloc.lower().removeprefix("www.")
        except Exception:
            host = ""
        if host and host not in groups:
            groups.append(host)
    for channel in channels or []:
        value = str(channel or "").strip().lower()
        if value and value not in groups:
            groups.append(value)
    return groups[:8]


def collect_news_summaries(hours: int = 6, limit: int = 180) -> list[SummaryItem]:
    """Collect fresh Telegram news without generating/sending the normal report."""
    settings = load_settings()
    channels = load_channels(settings.channel_config_path)
    if not channels:
        raise RuntimeError("No valid channels configured for insight watch")

    conn = connect(settings.database_path)
    init_db(conn)
    per_channel = int(os.getenv("INSIGHT_COLLECT_LIMIT_PER_CHANNEL", "120"))
    messages = asyncio.run(
        collect_messages(
            settings=settings,
            channels=channels,
            hours=hours,
            limit_per_channel=per_channel,
        )
    )
    inserted = insert_messages(conn, messages)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = fetch_recent(conn, since)
    deduped = deduplicate_rows(rows)
    print(
        f"[insight-watch] collected={len(messages)} inserted={inserted} "
        f"deduped={len(deduped)} hours={hours}"
    )
    return gemini_classify_if_available(
        deduped,
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        limit=limit,
    )


def _summary_rank(item: SummaryItem) -> int:
    impact_bonus = 10 if str(item.gemini_impact) == "높음" else 5 if str(item.gemini_impact) == "중간" else 0
    type_bonus = 6 if str(item.gemini_news_type) in {"거시", "실적", "리스크", "공시/확정", "이벤트"} else 0
    return int(item.importance_score or 0) + min(5, int(item.repeat_count or 1)) * 3 + impact_bonus + type_bonus


def build_current_evidence(summaries: list[SummaryItem], max_items: int = 80) -> list[dict[str, Any]]:
    ranked = sorted(summaries, key=_summary_rank, reverse=True)[:max_items]
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(ranked, 1):
        rows.append(
            {
                "id": f"E{index}",
                "title": str(item.title or "")[:180],
                "body": str(item.body or "")[:520],
                "judgment": str(item.judgment or "")[:240],
                "risk": str(item.risk or "")[:180],
                "sectors": list(item.sectors or [])[:8],
                "keywords": list(item.keywords or [])[:12],
                "tickers": list(item.tickers or [])[:10],
                "importance": int(item.importance_score or 0),
                "repeat_count": int(item.repeat_count or 1),
                "news_type": str(item.gemini_news_type or ""),
                "impact": str(item.gemini_impact or ""),
                "source_urls": list(item.source_urls or [])[:6],
                "source_groups": _source_groups(item.source_urls, item.channels),
                "message_dates": [str(value) for value in (item.message_dates or [])[:4]],
            }
        )
    return rows


def build_memory_evidence(now: datetime, max_items: int = 80) -> list[dict[str, Any]]:
    memory = _load_json(Path("reports/news_memory.json"), {"events": []})
    cutoff = now - timedelta(hours=MEMORY_HOURS)
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in memory.get("events") or []:
        try:
            seen = datetime.fromisoformat(str(item.get("last_seen") or "").replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=KST)
            seen = seen.astimezone(KST)
        except Exception:
            continue
        if seen < cutoff:
            continue
        age_hours = max(0.0, (now - seen).total_seconds() / 3600.0)
        recency = max(0.25, 1.0 - age_hours / max(1, MEMORY_HOURS))
        materiality = float(item.get("materiality") or 0)
        repeat = min(8, int(item.get("count") or 1))
        scored.append((materiality * recency + repeat * 5.0, item))
    scored.sort(key=lambda row: row[0], reverse=True)

    rows = []
    for index, (_score, item) in enumerate(scored[:max_items], 1):
        urls = [str(value) for value in (item.get("source_urls") or [])[:6]]
        rows.append(
            {
                "id": f"M{index}",
                "title": str(item.get("title") or "")[:190],
                "sectors": list(item.get("sectors") or [])[:8],
                "keywords": list(item.get("keywords") or [])[:12],
                "tickers": list(item.get("tickers") or [])[:10],
                "materiality": int(item.get("materiality") or 0),
                "count": int(item.get("count") or 1),
                "last_seen": str(item.get("last_seen") or ""),
                "source_urls": urls,
                "source_groups": _source_groups(urls),
            }
        )
    return rows


def collect_market_confirmation() -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    workers = min(10, max(2, int(os.getenv("INSIGHT_MARKET_WORKERS", "8"))))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="insight-market") as pool:
        futures = {pool.submit(fetch_asset_snapshot, ticker): ticker for ticker in INSIGHT_MARKET_ASSETS}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                item = future.result()
            except Exception as exc:
                item = {"ticker": ticker, "error": f"{type(exc).__name__}: {exc}"}
            assets[ticker] = {
                "ticker": ticker,
                "label": INSIGHT_MARKET_ASSETS[ticker],
                "price": _safe_float(item.get("price")),
                "change_pct": _safe_float(item.get("change_pct")),
                "return_5d": _safe_float(item.get("return_5d")),
                "return_20d": _safe_float(item.get("return_20d")),
                "session_date": item.get("session_date"),
                "error": item.get("error"),
            }
    return assets


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = str(text or "").strip()
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


def _json_model(prompt: str, api_key: str, model: str, max_tokens: int = 4200) -> tuple[dict[str, Any] | None, str]:
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.05,
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "application/json",
                },
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        raw = "".join(
            part.get("text", "")
            for part in (body.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        )
        return _extract_json(raw), "ok"
    except Exception as exc:
        return None, f"{type(exc).__name__}:{exc}"


def _synthesis_prompt(now: datetime, current: list[dict[str, Any]], memory: list[dict[str, Any]], market: dict[str, Any]) -> str:
    return f"""
너는 기관투자자 수준의 시장 구조·자금순환 인사이트 엔진이다.
현재 시각: {now:%Y-%m-%d %H:%M KST}

목표는 '뉴스를 요약'하는 것이 아니라 여러 독립 뉴스와 시장가격을 연결해 아직 한 문장으로 정리되지 않은 2차·3차 시장 논제를 찾는 것이다.
좋은 인사이트의 예시는 다음 유형이다. 예시 자체를 정답으로 앵커링하지 마라.
- 오래 오른 주도주의 피로/포지셔닝과 펀더멘털이 동시에 존재하면서 밸류에이션 콜이 반대편 종목군에 먹히는 회전
- 위험회피가 필요한데 장기금리 상승 때문에 채권이 피난처가 되지 못해 금·방어주·인컴상품 등으로 자금이 우회하는 구조
- 미국은 대체 섹터가 많은데 한국은 시장 폭/상품구조 때문에 탈출구가 제한되는 국가별 비대칭
- ETF 상품 출시·마케팅/포지셔닝/정책 변화가 투자자 심리의 후행지표가 되는 현상
- 실적 컨센서스는 상향인데 가격은 약해지는 등 '펀더멘털과 포지셔닝의 분리'

절대 규칙:
1. 단일 헤드라인, 단순 급등락, 누구나 아는 사실 재진술은 alert 대상이 아니다.
2. 최소 3개 evidence id를 연결해야 하며, 반드시 현재 뉴스 E*가 1개 이상 포함돼야 한다.
3. 같은 텔레그램 채널/같은 원문 복제는 독립 근거로 세지 않는다.
4. 인과관계는 '사실 → 해석 → 시장확인'을 분리한다. 근거 없는 인과는 쓰지 않는다.
5. 펀더멘털이 멀쩡하다는 주장은 실적/가이던스/컨센서스 근거가 있을 때만 한다.
6. '돈이 이동한다'는 주장은 섹터/ETF 가격확인 또는 복수의 수급·포지셔닝 뉴스가 있어야 한다.
7. BTC는 유동성/위험선호 확인용으로만 쓸 수 있고 코인 추천은 금지한다.
8. 매수·매도·목표가 지시는 금지한다. 사용자가 다음 시장 변화를 이해할 수 있는 논제만 제시한다.
9. 확신이 부족하면 should_alert=false로 한다. 평소에는 조용한 시스템이어야 한다.

점수(총 100):
- novelty 0~20: 단순 요약이 아닌 새 연결인가
- cross_signal 0~20: 뉴스/섹터/자산을 여러 축에서 연결하는가
- market_confirmation 0~20: 실제 가격이 논제를 확인하는가
- persistence 0~15: 최근 수일간 반복/누적된 구조인가
- source_diversity 0~15: 독립 출처가 충분한가
- decision_relevance 0~10: 향후 시장 레짐 판단에 의미가 큰가

반드시 아래 JSON 객체 하나만 반환:
{{
  "should_alert": true,
  "score": 0,
  "score_components": {{"novelty":0,"cross_signal":0,"market_confirmation":0,"persistence":0,"source_diversity":0,"decision_relevance":0}},
  "confidence": "high|medium|low",
  "thesis": "핵심 논제 한 문장",
  "chain": ["1번 인과", "2번 인과", "3번 인과", "4번 인과"],
  "conclusion": "결론",
  "why_now": "왜 지금 중요한지",
  "counterevidence": ["반증 또는 무효화 조건"],
  "watch_next": ["다음 확인 포인트"],
  "evidence_refs": ["E1","M2"],
  "new_trigger_refs": ["E1"],
  "market_confirmations": ["QQQ","XBI"],
  "theme_tags": ["AI포지셔닝피로","섹터순환"]
}}

CURRENT_EVIDENCE:
{json.dumps(current, ensure_ascii=False)}

HISTORICAL_MEMORY:
{json.dumps(memory, ensure_ascii=False)}

MARKET_CONFIRMATION:
{json.dumps(market, ensure_ascii=False)}
""".strip()


def synthesize_candidate(
    now: datetime,
    current: list[dict[str, Any]],
    memory: list[dict[str, Any]],
    market: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    api_key = _clean_secret(os.getenv("GEMINI_API_KEY"))
    if not api_key:
        return {"should_alert": False, "score": 0}, "gemini_api_key_missing"
    model = os.getenv("INSIGHT_GEMINI_MODEL", os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    result, error = _json_model(_synthesis_prompt(now, current, memory, market), api_key, model)
    if not result:
        return {"should_alert": False, "score": 0}, f"synthesis_failed:{error}"
    return result, f"Gemini({model})"


def _norm_tag(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def _candidate_signature(candidate: dict[str, Any]) -> str:
    tags = sorted({_norm_tag(value) for value in (candidate.get("theme_tags") or []) if _norm_tag(value)})
    source = "|".join(tags[:6])
    if len(tags) < 2:
        source = _norm_tag(str(candidate.get("thesis") or ""))[:180]
    return sha1(source.encode("utf-8")).hexdigest()


def _candidate_fingerprint(candidate: dict[str, Any], refs: list[str], market_refs: list[str]) -> str:
    conclusion = _norm_tag(str(candidate.get("conclusion") or ""))[:100]
    raw = "|".join(sorted(refs) + sorted(market_refs) + [conclusion])
    return sha256(raw.encode("utf-8")).hexdigest()


def _evidence_map(current: list[dict[str, Any]], memory: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in current + memory if item.get("id")}


def evaluate_candidate_gate(
    candidate: dict[str, Any],
    current: list[dict[str, Any]],
    memory: list[dict[str, Any]],
    market: dict[str, Any],
    state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    reasons: list[str] = []
    score = int(candidate.get("score") or 0)
    evidence = _evidence_map(current, memory)
    refs = [str(value) for value in (candidate.get("evidence_refs") or []) if str(value) in evidence]
    refs = list(dict.fromkeys(refs))
    new_refs = [str(value) for value in (candidate.get("new_trigger_refs") or []) if str(value).startswith("E") and str(value) in evidence]
    market_refs = [str(value) for value in (candidate.get("market_confirmations") or []) if str(value) in market and market[str(value)].get("price") is not None]
    market_refs = list(dict.fromkeys(market_refs))
    source_groups = sorted({group for ref in refs for group in evidence[ref].get("source_groups", []) if group})
    chain = [str(value).strip() for value in (candidate.get("chain") or []) if str(value).strip()]

    if not bool(candidate.get("should_alert")):
        reasons.append("model_below_alert_bar")
    if score < ALERT_SCORE:
        reasons.append(f"score_below_{ALERT_SCORE}")
    if len(chain) < 4:
        reasons.append("causal_chain_too_short")
    if len(refs) < 3:
        reasons.append("insufficient_evidence_refs")
    if not new_refs:
        reasons.append("no_fresh_news_trigger")
    if len(source_groups) < 2:
        reasons.append("insufficient_independent_sources")
    if len(market_refs) < 2 and len(source_groups) < 4:
        reasons.append("insufficient_market_or_source_confirmation")
    if not str(candidate.get("thesis") or "").strip() or not str(candidate.get("conclusion") or "").strip():
        reasons.append("missing_thesis_or_conclusion")

    signature = _candidate_signature(candidate)
    fingerprint = _candidate_fingerprint(candidate, refs, market_refs)
    prior = None
    for item in reversed(state.get("alerts") or []):
        if str(item.get("signature")) == signature:
            prior = item
            break
    if prior:
        try:
            alerted_at = datetime.fromisoformat(str(prior.get("alerted_at") or "").replace("Z", "+00:00"))
            if alerted_at.tzinfo is None:
                alerted_at = alerted_at.replace(tzinfo=KST)
            alerted_at = alerted_at.astimezone(KST)
        except Exception:
            alerted_at = now - timedelta(days=99)
        age = now - alerted_at
        prior_score = int(prior.get("score") or 0)
        if age < timedelta(hours=COOLDOWN_HOURS) and score < prior_score + 10:
            reasons.append("cooldown_same_thesis")
        if age < timedelta(hours=48) and str(prior.get("fingerprint") or "") == fingerprint:
            reasons.append("duplicate_evidence_fingerprint")

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "score": score,
        "refs": refs,
        "new_refs": new_refs,
        "market_refs": market_refs,
        "source_groups": source_groups,
        "signature": signature,
        "fingerprint": fingerprint,
    }


def _grounding_sources(candidate: dict[str, Any]) -> list[dict[str, str]]:
    metadata = candidate.get("groundingMetadata") or {}
    sources, seen = [], set()
    for chunk in metadata.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        uri = str(web.get("uri") or "").strip()
        title = str(web.get("title") or "").strip()
        if uri and uri not in seen:
            seen.add(uri)
            sources.append({"title": title, "uri": uri})
    return sources[:15]


def verify_candidate_with_google(
    candidate: dict[str, Any],
    gate: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    if os.getenv("INSIGHT_GOOGLE_VERIFY", "1") == "0":
        return {"passed": False, "reason": "google_verification_disabled", "sources": []}
    api_key = _clean_secret(os.getenv("GEMINI_API_KEY"))
    if not api_key:
        return {"passed": False, "reason": "gemini_api_key_missing", "sources": []}
    model = os.getenv("INSIGHT_GROUNDING_MODEL", os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    selected = [evidence[ref] for ref in gate.get("refs", []) if ref in evidence]
    prompt = f"""
You are the verification desk for a Korean market-insight alert. Current Seoul time: {now:%Y-%m-%d %H:%M KST}.
Use Google Search to verify the UNDERLYING FACTS of the causal chain below. Do not judge whether the thesis is a good trade. Verify claims such as earnings/consensus trends, sector rotation, yields, fund positioning, ETF/product-flow facts, policy changes, and cross-asset moves. Prefer official sources, company IR, exchange data, and major financial media. A broad inference can remain an inference, but its factual legs must be supported.

Return a concise memo with three headings: SUPPORTED, CONTRADICTED, UNVERIFIED. Each line should correspond to a materially distinct factual leg. Do not invent support.

THESIS: {candidate.get('thesis')}
CHAIN: {json.dumps(candidate.get('chain') or [], ensure_ascii=False)}
INTERNAL_EVIDENCE: {json.dumps(selected, ensure_ascii=False)}
""".strip()
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2600},
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        grounded = (body.get("candidates") or [{}])[0]
        raw = "".join(part.get("text", "") for part in (grounded.get("content") or {}).get("parts", []))
        sources = _grounding_sources(grounded)
        normalize_prompt = f"""
Convert the verification memo into JSON only. Do not add facts.
Schema: {{"supported":[""],"contradicted":[""],"unverified":[""]}}
MEMO:\n{raw[:12000]}
""".strip()
        normalized, error = _json_model(normalize_prompt, api_key, model, max_tokens=1800)
        if not normalized:
            return {"passed": False, "reason": f"verification_normalize_failed:{error}", "sources": sources}
        supported = [str(value) for value in (normalized.get("supported") or []) if str(value).strip()]
        contradicted = [str(value) for value in (normalized.get("contradicted") or []) if str(value).strip()]
        unverified = [str(value) for value in (normalized.get("unverified") or []) if str(value).strip()]
        chain_len = max(1, len(candidate.get("chain") or []))
        required = max(2, min(3, math.ceil(chain_len / 3)))
        passed = len(supported) >= required and not contradicted and len(sources) >= 2
        return {
            "passed": passed,
            "reason": "pass" if passed else "verification_threshold_not_met",
            "supported": supported,
            "contradicted": contradicted,
            "unverified": unverified,
            "sources": sources,
            "required_supported": required,
        }
    except Exception as exc:
        return {"passed": False, "reason": f"verification_request_failed:{type(exc).__name__}", "sources": []}


def _fmt_market(item: dict[str, Any]) -> str:
    change = _safe_float(item.get("change_pct"))
    ret5 = _safe_float(item.get("return_5d"))
    parts = [str(item.get("label") or item.get("ticker") or "?")]
    if change is not None:
        parts.append(f"1D {change:+.2f}%")
    if ret5 is not None:
        parts.append(f"5D {ret5:+.2f}%")
    return " ".join(parts)


def render_alert(
    candidate: dict[str, Any],
    gate: dict[str, Any],
    verification: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    market: dict[str, dict[str, Any]],
) -> str:
    confidence = {"high": "높음", "medium": "중간", "low": "낮음"}.get(str(candidate.get("confidence")), "중간")
    lines = [
        f"🧠 중요 시장 인사이트 | {gate.get('score', 0)}점 | 신뢰도 {confidence}",
        str(candidate.get("thesis") or "핵심 논제 확인불가"),
        "",
    ]
    for index, text in enumerate(candidate.get("chain") or [], 1):
        if str(text).strip():
            lines.append(f"{index}. {str(text).strip()}")
    lines += ["", f"결론. {candidate.get('conclusion') or '추가 확인 필요'}"]
    if candidate.get("why_now"):
        lines += ["", f"왜 지금 중요: {candidate['why_now']}"]

    counter = [str(value) for value in (candidate.get("counterevidence") or []) if str(value).strip()]
    if counter:
        lines += ["", "반증/무효화 조건"] + [f"- {value}" for value in counter[:4]]
    watch = [str(value) for value in (candidate.get("watch_next") or []) if str(value).strip()]
    if watch:
        lines += ["", "다음 확인 포인트"] + [f"- {value}" for value in watch[:4]]

    lines += ["", "대표 근거"]
    for ref in gate.get("refs", [])[:5]:
        item = evidence.get(ref) or {}
        groups = ", ".join((item.get("source_groups") or [])[:2]) or "출처미상"
        lines.append(f"- [{ref}] {item.get('title') or '제목없음'} · {groups}")

    market_refs = gate.get("market_refs") or []
    if market_refs:
        lines += ["", "시장 확인"]
        lines += [f"- {_fmt_market(market[ticker])}" for ticker in market_refs[:6] if ticker in market]

    lines += [
        "",
        f"검증: 내부근거 {len(gate.get('refs') or [])}개 · 독립출처 {len(gate.get('source_groups') or [])}개 · "
        f"Google 교차검증 {len(verification.get('sources') or [])}개",
        "※ 뉴스·가격을 연결한 시장구조 해석이며 매수·매도 지시가 아닙니다.",
    ]
    return "\n".join(lines).strip()


def _telegram_credentials() -> tuple[str, list[str]]:
    token = _clean_secret(os.getenv("TELEGRAM_BOT_TOKEN"))
    ids = []
    raw = str(os.getenv("TELEGRAM_TARGET_CHAT_IDS") or "")
    for value in raw.replace("\n", ",").split(","):
        value = value.strip().strip('"\'')
        if value and value not in ids:
            ids.append(value)
    single = str(os.getenv("TELEGRAM_TARGET_CHAT_ID") or "").strip().strip('"\'')
    if single and single not in ids:
        ids.insert(0, single)
    return token, ids


def _record_alert(state: dict[str, Any], candidate: dict[str, Any], gate: dict[str, Any], now: datetime) -> None:
    alerts = list(state.get("alerts") or [])
    alerts.append(
        {
            "signature": gate["signature"],
            "fingerprint": gate["fingerprint"],
            "thesis": str(candidate.get("thesis") or ""),
            "score": int(gate.get("score") or 0),
            "alerted_at": now.isoformat(timespec="seconds"),
            "theme_tags": list(candidate.get("theme_tags") or [])[:8],
        }
    )
    state["alerts"] = alerts[-100:]
    state["version"] = 1
    state["updated_at"] = now.isoformat(timespec="seconds")
    _write_json(STATE_PATH, state)


def run_watch(hours: int = 6, limit: int = 180, send: bool = False, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(KST)
    state = _load_json(STATE_PATH, {"version": 1, "alerts": []})
    summaries = collect_news_summaries(hours=hours, limit=limit)
    current = build_current_evidence(summaries)
    memory = build_memory_evidence(now)
    market = collect_market_confirmation()
    candidate, engine = synthesize_candidate(now, current, memory, market)
    gate = evaluate_candidate_gate(candidate, current, memory, market, state, now)
    evidence = _evidence_map(current, memory)
    verification: dict[str, Any] = {"passed": False, "reason": "candidate_gate_failed", "sources": []}
    sent = False
    alert_text = ""

    if gate["eligible"]:
        verification = verify_candidate_with_google(candidate, gate, evidence, now)
        if verification.get("passed"):
            alert_text = render_alert(candidate, gate, verification, evidence, market)
            if send:
                token, chat_ids = _telegram_credentials()
                if not token or not chat_ids:
                    raise RuntimeError("Insight alert requires TELEGRAM_BOT_TOKEN and TELEGRAM_TARGET_CHAT_ID(S)")
                send_telegram_message_to_many(bot_token=token, chat_ids=chat_ids, text=alert_text)
                _record_alert(state, candidate, gate, now)
                sent = True

    result = {
        "generated_at": now.isoformat(timespec="seconds"),
        "hours": hours,
        "summary_count": len(summaries),
        "current_evidence_count": len(current),
        "memory_evidence_count": len(memory),
        "market_asset_count": sum(1 for item in market.values() if item.get("price") is not None),
        "engine": engine,
        "candidate": candidate,
        "gate": gate,
        "verification": verification,
        "would_alert": bool(gate["eligible"] and verification.get("passed")),
        "sent": sent,
        "alert_text": alert_text,
    }
    _write_json(LATEST_PATH, result)
    print(
        f"[insight-watch] score={gate.get('score')} eligible={gate.get('eligible')} "
        f"verified={verification.get('passed')} sent={sent} reasons={','.join(gate.get('reasons') or []) or 'none'}"
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Background market insight watch")
    parser.add_argument("--hours", type=int, default=6)
    parser.add_argument("--limit", type=int, default=180)
    parser.add_argument("--send", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_watch(hours=args.hours, limit=args.limit, send=args.send)
    print(json.dumps({"would_alert": result["would_alert"], "sent": result["sent"], "score": result["gate"]["score"], "reasons": result["gate"]["reasons"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
