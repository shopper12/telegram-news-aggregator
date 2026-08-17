from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
STATE_PATH = Path(os.getenv("ADAPTIVE_STRATEGY_STATE_PATH", "reports/adaptive_strategy_state.json"))
LEDGER_PATH = Path(os.getenv("STRATEGY_LEDGER_PATH", "reports/strategy_ledger.json"))
MEMORY_PATH = Path(os.getenv("NEWS_MEMORY_PATH", "reports/news_memory.json"))
MEMORY_RETENTION_HOURS = int(os.getenv("NEWS_MEMORY_RETENTION_HOURS", "168"))
MAX_MEMORY_EVENTS = int(os.getenv("NEWS_MEMORY_MAX_EVENTS", "500"))
MAX_LEDGER_ITEMS = int(os.getenv("STRATEGY_LEDGER_MAX_ITEMS", "500"))
MODEL_COMPONENTS = ("momentum", "regime", "news", "defensive")
HORIZONS = {"6h": timedelta(hours=6), "24h": timedelta(hours=24), "72h": timedelta(hours=72)}
MIN_AUTO_WEIGHT_SAMPLES = int(os.getenv("ADAPTIVE_WEIGHT_MIN_SAMPLES", "200"))
MAX_WEIGHT_CHANGE_PER_CYCLE = float(os.getenv("ADAPTIVE_WEIGHT_MAX_STEP", "0.05"))
WEIGHT_MIN = float(os.getenv("ADAPTIVE_WEIGHT_MIN", "0.50"))
WEIGHT_MAX = float(os.getenv("ADAPTIVE_WEIGHT_MAX", "1.50"))


def now_kst() -> datetime:
    return datetime.now(KST)


def _default_stats() -> dict[str, Any]:
    return {
        "evaluated_24h": 0,
        "wins_24h": 0,
        "losses_24h": 0,
        "flat_24h": 0,
        "average_return_24h_pct": 0.0,
        "average_win_24h_pct": 0.0,
        "average_loss_24h_pct": 0.0,
        "payoff_ratio_24h": None,
        "max_drawdown_24h_pct": 0.0,
        "weight_adjustment_active": False,
        "weight_min_samples": MIN_AUTO_WEIGHT_SAMPLES,
        "weight_max_step": MAX_WEIGHT_CHANGE_PER_CYCLE,
    }


def default_state() -> dict[str, Any]:
    return {
        "version": 2,
        "updated_at": now_kst().isoformat(timespec="seconds"),
        "weights": {name: 1.0 for name in MODEL_COMPONENTS},
        "stats": _default_stats(),
        "asset_stats": {},
        "last_run": {},
    }


def default_ledger() -> dict[str, Any]:
    return {"version": 1, "updated_at": now_kst().isoformat(timespec="seconds"), "recommendations": []}


def default_memory() -> dict[str, Any]:
    return {"version": 2, "updated_at": now_kst().isoformat(timespec="seconds"), "events": []}


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(fallback))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else json.loads(json.dumps(fallback))
    except Exception:
        return json.loads(json.dumps(fallback))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    state.setdefault("weights", {name: 1.0 for name in MODEL_COMPONENTS})
    for name in MODEL_COMPONENTS:
        try:
            state["weights"][name] = float(state["weights"].get(name, 1.0))
        except Exception:
            state["weights"][name] = 1.0
    stats = state.setdefault("stats", {})
    for key, value in _default_stats().items():
        stats.setdefault(key, value)
    state.setdefault("asset_stats", {})
    state.setdefault("last_run", {})
    state["version"] = max(2, int(state.get("version") or 1))
    return state


def load_runtime_state() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = _migrate_state(load_json(STATE_PATH, default_state()))
    ledger = load_json(LEDGER_PATH, default_ledger())
    memory = load_json(MEMORY_PATH, default_memory())
    return state, ledger, memory


def save_runtime_state(state: dict[str, Any], ledger: dict[str, Any], memory: dict[str, Any]) -> None:
    save_json(STATE_PATH, state)
    save_json(LEDGER_PATH, ledger)
    save_json(MEMORY_PATH, memory)


def parse_dt(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None


def _merge_unique(existing: Any, incoming: Any, limit: int) -> list[str]:
    result: list[str] = []
    for value in list(existing or []) + list(incoming or []):
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def update_news_memory(memory: dict[str, Any], incoming: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    now = now or now_kst()
    existing = {str(item.get("signature")): dict(item) for item in memory.get("events", []) if item.get("signature")}
    for event in incoming:
        signature = str(event.get("signature") or "")
        if not signature:
            continue
        if signature not in existing:
            created = dict(event)
            created["source_urls"] = _merge_unique([], event.get("source_urls"), 5)
            created["keywords"] = _merge_unique([], event.get("keywords"), 10)
            created["tickers"] = _merge_unique([], event.get("tickers"), 8)
            existing[signature] = created
            continue
        previous = existing[signature]
        previous["last_seen"] = event.get("last_seen") or now.isoformat(timespec="seconds")
        previous["count"] = int(previous.get("count") or 1) + 1
        previous["materiality"] = max(int(previous.get("materiality") or 0), int(event.get("materiality") or 0))
        previous["sentiment"] = int(event.get("sentiment") if event.get("sentiment") is not None else previous.get("sentiment") or 0)
        previous["sectors"] = _merge_unique(previous.get("sectors"), event.get("sectors"), 6)
        previous["keywords"] = _merge_unique(previous.get("keywords"), event.get("keywords"), 10)
        previous["tickers"] = _merge_unique(previous.get("tickers"), event.get("tickers"), 8)
        previous["source_urls"] = _merge_unique(previous.get("source_urls"), event.get("source_urls"), 5)

    cutoff = now - timedelta(hours=MEMORY_RETENTION_HOURS)
    kept = []
    for item in existing.values():
        last_seen = parse_dt(item.get("last_seen")) or now
        if last_seen >= cutoff:
            kept.append(item)
    kept.sort(key=lambda item: str(item.get("last_seen") or ""), reverse=True)
    memory["events"] = kept[:MAX_MEMORY_EVENTS]
    memory["updated_at"] = now.isoformat(timespec="seconds")
    memory["version"] = max(2, int(memory.get("version") or 1))
    return memory


def append_recommendations(ledger: dict[str, Any], recommendations: list[dict[str, Any]], now: datetime | None = None) -> None:
    now = now or now_kst()
    known = {str(item.get("id")) for item in ledger.get("recommendations", [])}
    for item in recommendations:
        if str(item.get("id")) not in known:
            ledger.setdefault("recommendations", []).append(item)
    ledger["recommendations"] = ledger.get("recommendations", [])[-MAX_LEDGER_ITEMS:]
    ledger["updated_at"] = now.isoformat(timespec="seconds")


def evaluate_open_recommendations(ledger: dict[str, Any], snapshot: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or now_kst()
    updates = []
    assets = snapshot.get("assets") or {}
    for item in ledger.get("recommendations", []):
        if item.get("status") != "open":
            continue
        created = parse_dt(item.get("created_at"))
        try:
            entry = float(item.get("entry_price"))
            current = float((assets.get(str(item.get("ticker"))) or {}).get("price"))
        except Exception:
            continue
        if not created or entry <= 0:
            continue
        elapsed = now - created
        return_pct = (current - entry) / entry * 100.0
        evaluations = item.setdefault("evaluations", {})
        for label, horizon in HORIZONS.items():
            if elapsed >= horizon and label not in evaluations:
                evaluations[label] = {"evaluated_at": now.isoformat(timespec="seconds"), "price": round(current, 6), "return_pct": round(return_pct, 4)}
                updates.append({"id": item.get("id"), "horizon": label, "return_pct": return_pct})
        stop = float(item.get("stop_price") or 0)
        target = float(item.get("target_price") or 0)
        if target and current >= target:
            item["status"], item["outcome"] = "closed", "target_observed"
        elif stop and current <= stop:
            item["status"], item["outcome"] = "closed", "stop_observed"
        elif elapsed >= HORIZONS["72h"]:
            item["status"], item["outcome"] = "closed", "time_exit"
        if item.get("status") == "closed":
            item["closed_at"] = now.isoformat(timespec="seconds")
            item["final_return_pct"] = round(return_pct, 4)
    ledger["updated_at"] = now.isoformat(timespec="seconds")
    return updates


def _24h_results(ledger: dict[str, Any]) -> list[tuple[dict[str, Any], float, datetime | None]]:
    rows: list[tuple[dict[str, Any], float, datetime | None]] = []
    for item in ledger.get("recommendations", []):
        result = (item.get("evaluations") or {}).get("24h")
        if not isinstance(result, dict):
            continue
        try:
            return_pct = float(result.get("return_pct") or 0.0)
        except Exception:
            continue
        when = parse_dt(result.get("evaluated_at")) or parse_dt(item.get("created_at"))
        rows.append((item, return_pct, when))
    rows.sort(key=lambda row: row[2] or datetime.min.replace(tzinfo=KST))
    return rows


def _max_drawdown_pct(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for return_pct in returns:
        equity *= max(0.000001, 1.0 + return_pct / 100.0)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        max_dd = max(max_dd, drawdown)
    return max_dd


def _recompute_stats(state: dict[str, Any], ledger: dict[str, Any]) -> list[tuple[dict[str, Any], float, datetime | None]]:
    rows = _24h_results(ledger)
    returns = [return_pct for _, return_pct, _ in rows]
    wins = [value for value in returns if value > 0.2]
    losses = [value for value in returns if value < -0.2]
    flats = [value for value in returns if -0.2 <= value <= 0.2]
    avg = sum(returns) / len(returns) if returns else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = avg_win / abs(avg_loss) if wins and losses and avg_loss else None
    stats = state.setdefault("stats", _default_stats())
    stats.update(
        {
            "evaluated_24h": len(returns),
            "wins_24h": len(wins),
            "losses_24h": len(losses),
            "flat_24h": len(flats),
            "average_return_24h_pct": round(avg, 4),
            "average_win_24h_pct": round(avg_win, 4),
            "average_loss_24h_pct": round(avg_loss, 4),
            "payoff_ratio_24h": round(payoff, 4) if payoff is not None and math.isfinite(payoff) else None,
            "max_drawdown_24h_pct": round(_max_drawdown_pct(returns), 4),
            "weight_adjustment_active": len(returns) >= MIN_AUTO_WEIGHT_SAMPLES,
            "weight_min_samples": MIN_AUTO_WEIGHT_SAMPLES,
            "weight_max_step": MAX_WEIGHT_CHANGE_PER_CYCLE,
        }
    )
    return rows


def _historical_component_edge(rows: list[tuple[dict[str, Any], float, datetime | None]], name: str) -> float:
    aligned_scores: list[float] = []
    for item, return_pct, _when in rows:
        signal = float((item.get("components") or {}).get(name) or 0.0)
        if abs(signal) < 1e-9 or abs(return_pct) <= 0.2:
            continue
        aligned = 1.0 if (signal > 0) == (return_pct > 0) else -1.0
        strength = min(1.0, abs(signal) / 4.0)
        aligned_scores.append(aligned * strength)
    return sum(aligned_scores) / len(aligned_scores) if aligned_scores else 0.0


def _update_asset_stats(state: dict[str, Any], item: dict[str, Any], return_pct: float) -> None:
    ticker = str(item.get("ticker") or "")
    if not ticker:
        return
    asset = state.setdefault("asset_stats", {}).setdefault(ticker, {"evaluated": 0, "wins": 0, "average_return_pct": 0.0})
    count = int(asset.get("evaluated") or 0)
    average = float(asset.get("average_return_pct") or 0.0)
    asset["evaluated"] = count + 1
    asset["average_return_pct"] = round((average * count + return_pct) / (count + 1), 4)
    if return_pct > 0.2:
        asset["wins"] = int(asset.get("wins") or 0) + 1


def adapt_model_from_results(state: dict[str, Any], ledger: dict[str, Any], now: datetime | None = None) -> int:
    """Update performance statistics and, only with enough samples, model weights.

    - Fewer than ``MIN_AUTO_WEIGHT_SAMPLES`` verified 24h outcomes: every weight
      is forced to 1.00, including legacy state loaded from disk.
    - Once the threshold is reached, a cycle can move each component by at most
      ``MAX_WEIGHT_CHANGE_PER_CYCLE``. Direction is based on historical alignment
      across all available 24h outcomes, not on one recent trade.
    """
    now = now or now_kst()
    _migrate_state(state)
    weights = state.setdefault("weights", {name: 1.0 for name in MODEL_COMPONENTS})

    learned = 0
    for item in ledger.get("recommendations", []):
        result = (item.get("evaluations") or {}).get("24h")
        if not result or item.get("learned_24h"):
            continue
        try:
            return_pct = float(result.get("return_pct") or 0.0)
        except Exception:
            continue
        _update_asset_stats(state, item, return_pct)
        item["learned_24h"] = True
        item["learned_at"] = now.isoformat(timespec="seconds")
        learned += 1

    rows = _recompute_stats(state, ledger)
    sample_count = len(rows)
    if sample_count < MIN_AUTO_WEIGHT_SAMPLES:
        for name in MODEL_COMPONENTS:
            weights[name] = 1.0
    elif learned > 0:
        max_step = max(0.0, min(0.05, float(MAX_WEIGHT_CHANGE_PER_CYCLE)))
        for name in MODEL_COMPONENTS:
            edge = _historical_component_edge(rows, name)
            proposed_delta = max(-max_step, min(max_step, edge * max_step))
            current = float(weights.get(name, 1.0))
            weights[name] = round(max(WEIGHT_MIN, min(WEIGHT_MAX, current + proposed_delta)), 4)

    state["updated_at"] = now.isoformat(timespec="seconds")
    state.setdefault("stats", {})["weight_adjustment_active"] = sample_count >= MIN_AUTO_WEIGHT_SAMPLES
    return learned
