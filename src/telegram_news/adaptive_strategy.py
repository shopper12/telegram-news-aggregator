from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha1
import os
import sys
from typing import Any
from zoneinfo import ZoneInfo

from . import strict_report_v2 as base_report
from .global_market_tracker import TRADE_ASSETS, collect_global_snapshot
from .market_outlook import NEGATIVE_WORDS, POSITIVE_WORDS
from .strategy_learning import (
    MEMORY_RETENTION_HOURS,
    MODEL_COMPONENTS,
    adapt_model_from_results,
    append_recommendations,
    evaluate_open_recommendations,
    load_runtime_state,
    now_kst,
    save_runtime_state,
    update_news_memory,
)
from .strict_quality import materiality_score


KST = ZoneInfo("Asia/Seoul")


def _hits(text: str, words: set[str]) -> int:
    lower = str(text or "").lower()
    return sum(1 for word in words if word.lower() in lower)


def _selected_news(selected: list[Any], now: datetime) -> list[dict[str, Any]]:
    events = []
    timestamp = now.isoformat(timespec="seconds")
    for cluster in selected[:50]:
        try:
            title = base_report._display_title(cluster, 120)
            text = base_report._cluster_text(cluster)
            sectors = [str(value) for value in (cluster.sectors() or []) if str(value).strip()]
            score = int(materiality_score(cluster))
        except Exception:
            continue
        sentiment = max(-3, min(3, _hits(text, POSITIVE_WORDS) - _hits(text, NEGATIVE_WORDS)))
        signature = sha1(f"{title}|{','.join(sectors[:4])}".encode("utf-8")).hexdigest()
        events.append({"signature": signature, "title": title, "sectors": sectors[:6], "materiality": score, "sentiment": sentiment, "first_seen": timestamp, "last_seen": timestamp, "count": 1})
    return events


def _safe(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def _news_score(meta: dict[str, Any], memory: dict[str, Any], now: datetime) -> tuple[float, list[str]]:
    keywords = [str(value).lower() for value in meta.get("keywords", [])]
    cutoff = now - timedelta(hours=24)
    total = 0.0
    reasons = []
    for event in memory.get("events", []):
        try:
            seen = datetime.fromisoformat(str(event.get("last_seen")).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=KST)
            seen = seen.astimezone(KST)
        except Exception:
            continue
        if seen < cutoff:
            continue
        haystack = f"{event.get('title') or ''} {' '.join(event.get('sectors') or [])}".lower()
        if not any(keyword in haystack for keyword in keywords if keyword):
            continue
        recency = max(0.2, 1.0 - (now - seen).total_seconds() / 86400)
        contribution = float(event.get("sentiment") or 0) * float(event.get("materiality") or 0) / 100.0 * recency
        total += contribution
        reasons.append((abs(contribution), str(event.get("title") or "")))
    reasons.sort(reverse=True)
    return max(-4.0, min(4.0, total)), [title for _, title in reasons[:2] if title]


def _momentum(item: dict[str, Any]) -> float:
    daily = _safe(item.get("change_pct")) or 0.0
    ret5 = _safe(item.get("return_5d")) or 0.0
    ret20 = _safe(item.get("return_20d")) or 0.0
    return max(-4.0, min(4.0, (daily * 0.35 + ret5 * 0.45 + ret20 * 0.20) / 2.0))


def _regime(group: str, regime: str) -> float:
    if regime == "risk_on":
        return 2.0 if group == "risk" else -0.7 if group == "defensive" else 0.4
    if regime == "risk_off":
        return -2.0 if group == "risk" else 2.0 if group == "defensive" else 0.6
    return 0.2 if group in {"defensive", "commodity"} else 0.0


def _defensive(group: str, snapshot: dict[str, Any]) -> float:
    assets = snapshot.get("assets") or {}
    vix = _safe((assets.get("^VIX") or {}).get("change_pct"))
    tnx = _safe((assets.get("^TNX") or {}).get("change_pct"))
    score = 0.0
    if group == "defensive":
        score += 1.0 if vix is not None and vix > 2 else 0.0
        score += 0.7 if tnx is not None and tnx < -0.5 else 0.0
    elif group == "risk":
        score -= 1.0 if vix is not None and vix > 5 else 0.0
        score -= 0.5 if tnx is not None and tnx > 1 else 0.0
    return score


def _candidates(snapshot: dict[str, Any], memory: dict[str, Any], state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    weights = state.get("weights") or {}
    out = []
    for ticker, meta in TRADE_ASSETS.items():
        item = (snapshot.get("assets") or {}).get(ticker) or {}
        price = _safe(item.get("price"))
        if price is None:
            continue
        news, reasons = _news_score(meta, memory, now)
        components = {"momentum": _momentum(item), "regime": _regime(meta["group"], str(snapshot.get("regime"))), "news": news, "defensive": _defensive(meta["group"], snapshot)}
        total = sum(float(weights.get(name, 1.0)) * value for name, value in components.items())
        out.append({"ticker": ticker, "asset": meta["name"], "group": meta["group"], "price": price, "volatility": _safe(item.get("volatility_20d")) or 2.0, "components": components, "score": round(total, 3), "news_reasons": reasons})
    return sorted(out, key=lambda item: item["score"], reverse=True)


def _recommendation(candidate: dict[str, Any], slot: str, state: dict[str, Any], now: datetime) -> dict[str, Any]:
    price = float(candidate["price"])
    stop_pct = max(2.0, min(8.0, float(candidate["volatility"]) * 1.6))
    target_pct = max(4.0, min(16.0, stop_pct * 2.0))
    reasons = candidate.get("news_reasons") or []
    reason = f"글로벌 레짐 {candidate['components']['regime']:+.2f}, 모멘텀 {candidate['components']['momentum']:+.2f}, 뉴스 {candidate['components']['news']:+.2f}"
    if reasons:
        reason += " / 연결 뉴스: " + "; ".join(reasons)
    return {
        "id": sha1(f"{slot}|{candidate['ticker']}|{now.isoformat(timespec='minutes')}".encode("utf-8")).hexdigest()[:16],
        "created_at": now.isoformat(timespec="seconds"),
        "slot": slot,
        "ticker": candidate["ticker"],
        "asset": candidate["asset"],
        "direction": "LONG",
        "entry_price": round(price, 6),
        "entry_zone": [round(price * 0.995, 6), round(price * 1.005, 6)],
        "stop_price": round(price * (1 - stop_pct / 100), 6),
        "target_price": round(price * (1 + target_pct / 100), 6),
        "score": candidate["score"],
        "components": candidate["components"],
        "component_text": ", ".join(f"{name} {value:+.2f}" for name, value in candidate["components"].items()),
        "weights": dict(state.get("weights") or {}),
        "reason": reason,
        "status": "open",
        "evaluations": {},
        "learned_24h": False,
    }


def generate_recommendations(snapshot: dict[str, Any], memory: dict[str, Any], state: dict[str, Any], slot: str, now: datetime) -> list[dict[str, Any]]:
    minimum = float(os.getenv("STRATEGY_MIN_SCORE", "1.5"))
    selected, risk_count, defensive_count = [], 0, 0
    for candidate in _candidates(snapshot, memory, state, now):
        if candidate["score"] < minimum:
            continue
        if candidate["group"] == "risk" and risk_count >= 2:
            continue
        if candidate["group"] == "defensive" and defensive_count >= 1:
            continue
        selected.append(_recommendation(candidate, slot, state, now))
        risk_count += candidate["group"] == "risk"
        defensive_count += candidate["group"] == "defensive"
        if len(selected) == 3:
            break
    return selected


def _slot(kind: str) -> str | None:
    kind = str(kind or "").lower()
    if kind == "strategy_morning":
        return "morning"
    if kind == "strategy_evening":
        return "evening"
    return "manual" if os.getenv("ADAPTIVE_STRATEGY_FORCE", "0") == "1" else None


def _fmt(value: Any) -> str:
    number = _safe(value)
    if number is None:
        return "확인불가"
    return f"{number:,.0f}" if number >= 1000 else f"{number:,.2f}" if number >= 10 else f"{number:,.4f}"


def _performance(state: dict[str, Any]) -> str:
    stats = state.get("stats") or {}
    count = int(stats.get("evaluated_24h") or 0)
    wins = int(stats.get("wins_24h") or 0)
    win_rate = wins / count * 100 if count else 0.0
    weights = state.get("weights") or {}
    weight_text = ", ".join(f"{name} {float(weights.get(name, 1.0)):.2f}" for name in MODEL_COMPONENTS)
    return f"24시간 검증 {count}건 · 승률 {win_rate:.1f}% · 평균 {float(stats.get('average_return_24h_pct') or 0):+.2f}% · 가중치 {weight_text}"


def build_strategy_section(snapshot: dict[str, Any], memory: dict[str, Any], state: dict[str, Any], ledger: dict[str, Any], slot: str | None, recommendations: list[dict[str, Any]], evaluations: list[dict[str, Any]], learned: int) -> str:
    lines = [
        "🌐 글로벌 시황·수급 프록시",
        f"  • 레짐: {snapshot.get('regime_label', '확인불가')} | 점수 {float(snapshot.get('regime_score') or 0):+.2f}",
        f"  • 지역: {snapshot.get('regions') or '확인불가'}",
        f"  • 자금 흐름 프록시: {snapshot.get('flow_proxy') or '확인불가'}",
        f"  • 데이터: {snapshot.get('data_quality', 0)}/{snapshot.get('requested_assets', 0)} 자산 확인",
        "  • 주의: ETF·지수·금리·변동성 가격을 이용한 수급 프록시이며 실시간 순매수 원자료와 동일하지 않음",
        "",
        "🧠 지속학습 상태",
        f"  • 뉴스 메모리: 최근 {MEMORY_RETENTION_HOURS}시간 {len(memory.get('events', []))}개 이슈",
        f"  • 전략 원장: 진행 {sum(item.get('status') == 'open' for item in ledger.get('recommendations', []))}건 · 이번 평가 {len(evaluations)}건 · 이번 학습 {learned}건",
        f"  • 누적 성과: {_performance(state)}",
    ]
    if not slot:
        lines.append("  • 정식 전략은 매일 07:00 아침과 22:30 저녁 실행에서 생성")
        return "\n".join(lines)
    label = "아침" if slot == "morning" else "저녁" if slot == "evening" else "수동"
    lines += ["", f"🎯 {label} 글로벌 매매전략"]
    if not recommendations:
        lines += ["  • 판정: 신규 진입 보류", "  • 이유: 데이터 부족 또는 최소 점수 미달", "  • 대응: 현금 비중 유지 후 다음 30분 재평가"]
    for index, item in enumerate(recommendations, 1):
        zone = item["entry_zone"]
        lines += [
            f"{index}) {item['asset']}({item['ticker']}) LONG | 점수 {float(item['score']):+.2f}",
            f"  • 진입구간: {_fmt(zone[0])} ~ {_fmt(zone[1])} | 기준가 {_fmt(item['entry_price'])}",
            f"  • 손절/무효: {_fmt(item['stop_price'])} | 목표: {_fmt(item['target_price'])}",
            f"  • 구성점수: {item['component_text']}",
            f"  • 근거: {item['reason']}",
        ]
    lines += ["  • 검증: 6시간·24시간·72시간 가격을 기록하고 24시간 결과로 가중치를 조정", "  • 주의: 체결·세금·슬리피지·개인 위험한도를 반영하지 않은 자동 생성 전략"]
    return "\n".join(lines)


def run_adaptive_cycle(selected: list[Any], kind: str, now: datetime | None = None, snapshot: dict[str, Any] | None = None) -> str:
    now = now or now_kst()
    state, ledger, memory = load_runtime_state()
    memory = update_news_memory(memory, _selected_news(selected, now), now)
    snapshot = snapshot or collect_global_snapshot()
    evaluations = evaluate_open_recommendations(ledger, snapshot, now)
    learned = adapt_model_from_results(state, ledger, now)
    slot = _slot(kind)
    recommendations = generate_recommendations(snapshot, memory, state, slot, now) if slot else []
    append_recommendations(ledger, recommendations, now)
    state["last_run"] = {"timestamp": now.isoformat(timespec="seconds"), "kind": kind, "slot": slot, "regime": snapshot.get("regime"), "regime_score": snapshot.get("regime_score"), "recommendation_ids": [item["id"] for item in recommendations]}
    state["updated_at"] = now.isoformat(timespec="seconds")
    save_runtime_state(state, ledger, memory)
    return build_strategy_section(snapshot, memory, state, ledger, slot, recommendations, evaluations, learned)


def _insert_strategy(report: str, section: str) -> str:
    lines = report.splitlines()
    index = next((i for i, line in enumerate(lines) if line.startswith("선별방식:")), None)
    if index is None:
        index = next((i for i, line in enumerate(lines) if line.startswith("📌 핵심 이슈")), min(6, len(lines)))
    lines[index:index] = [section, ""]
    merged = "\n".join(lines).strip()
    limit = int(getattr(base_report, "MAX_REPORT_CHARS", 12000))
    return merged[: limit - 20] + "\n… 이하 생략" if len(merged) > limit else merged


def install() -> None:
    current = base_report.build_markdown_report
    if getattr(current, "_adaptive_strategy_installed", False):
        return
    original = current

    def wrapped(summaries, hours: int, timezone_name: str = "Asia/Seoul") -> str:
        report = original(summaries, hours, timezone_name)
        if not report:
            return report
        try:
            selected, *_ = base_report.s._select_strict(summaries)
            selected = base_report._drop_noise(selected)
        except Exception:
            selected = []
        try:
            section = run_adaptive_cycle(selected, os.getenv("BRIEFING_KIND", "regular"))
        except Exception as exc:
            section = f"🌐 글로벌 적응형 전략 엔진\n  • 상태: 실행 실패 {type(exc).__name__}: {exc}\n  • 기존 뉴스 리포트 발송은 계속 진행"
            print(f"[adaptive-strategy] cycle failed: {type(exc).__name__}: {exc}")
        return _insert_strategy(report, section)

    wrapped._adaptive_strategy_installed = True
    wrapped._adaptive_strategy_original = original
    base_report.build_markdown_report = wrapped
    app_module = sys.modules.get("telegram_news.app")
    if app_module is not None:
        setattr(app_module, "build_markdown_report", wrapped)
    print("[adaptive-strategy] global tracking, strategy ledger, evaluation, and online calibration installed")
