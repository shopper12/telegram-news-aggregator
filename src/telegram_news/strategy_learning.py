from __future__ import annotations

from datetime import datetime, timedelta
import json
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


def now_kst() -> datetime:
    return datetime.now(KST)


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": now_kst().isoformat(timespec="seconds"),
        "weights": {name: 1.0 for name in MODEL_COMPONENTS},
        "stats": {"evaluated_24h": 0, "wins_24h": 0, "losses_24h": 0, "flat_24h": 0, "average_return_24h_pct": 0.0},
        "asset_stats": {},
        "last_run": {},
    }


def default_ledger() -> dict[str, Any]:
    return {"version": 1, "updated_at": now_kst().isoformat(timespec="seconds"), "recommendations": []}


def default_memory() -> dict[str, Any]:
    return {"version": 1, "updated_at": now_kst().isoformat(timespec="seconds"), "events": []}


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


def load_runtime_state() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return load_json(STATE_PATH, default_state()), load_json(LEDGER_PATH, default_ledger()), load_json(MEMORY_PATH, default_memory())


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


def update_news_memory(memory: dict[str, Any], incoming: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    now = now or now_kst()
    existing = {str(item.get("signature")): dict(item) for item in memory.get("events", []) if item.get("signature")}
    for event in incoming:
        signature = str(event.get("signature") or "")
        if not signature:
            continue
        if signature not in existing:
            existing[signature] = dict(event)
            continue
        previous = existing[signature]
        previous["last_seen"] = event.get("last_seen") or now.isoformat(timespec="seconds")
        previous["count"] = int(previous.get("count") or 1) + 1
        previous["materiality"] = max(int(previous.get("materiality") or 0), int(event.get("materiality") or 0))
        previous["sentiment"] = int(event.get("sentiment") or previous.get("sentiment") or 0)
        previous["sectors"] = list(dict.fromkeys(list(previous.get("sectors") or []) + list(event.get("sectors") or [])))[:6]

    cutoff = now - timedelta(hours=MEMORY_RETENTION_HOURS)
    kept = []
    for item in existing.values():
        last_seen = parse_dt(item.get("last_seen")) or now
        if last_seen >= cutoff:
            kept.append(item)
    kept.sort(key=lambda item: str(item.get("last_seen") or ""), reverse=True)
    memory["events"] = kept[:MAX_MEMORY_EVENTS]
    memory["updated_at"] = now.isoformat(timespec="seconds")
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


def adapt_model_from_results(state: dict[str, Any], ledger: dict[str, Any], now: datetime | None = None) -> int:
    now = now or now_kst()
    weights = state.setdefault("weights", {name: 1.0 for name in MODEL_COMPONENTS})
    stats = state.setdefault("stats", default_state()["stats"])
    learned = 0
    for item in ledger.get("recommendations", []):
        result = (item.get("evaluations") or {}).get("24h")
        if not result or item.get("learned_24h"):
            continue
        return_pct = float(result.get("return_pct") or 0.0)
        outcome = 1 if return_pct > 0.2 else -1 if return_pct < -0.2 else 0
        for name in MODEL_COMPONENTS:
            signal = float((item.get("components") or {}).get(name) or 0.0)
            aligned = outcome and signal and ((signal > 0) == (outcome > 0))
            delta = 0.03 if aligned else -0.03 if outcome and signal else 0.0
            weights[name] = round(max(0.5, min(1.5, float(weights.get(name, 1.0)) + delta)), 4)

        count = int(stats.get("evaluated_24h") or 0)
        average = float(stats.get("average_return_24h_pct") or 0.0)
        stats["evaluated_24h"] = count + 1
        stats["average_return_24h_pct"] = round((average * count + return_pct) / (count + 1), 4)
        key = "wins_24h" if outcome > 0 else "losses_24h" if outcome < 0 else "flat_24h"
        stats[key] = int(stats.get(key) or 0) + 1

        ticker = str(item.get("ticker") or "")
        asset = state.setdefault("asset_stats", {}).setdefault(ticker, {"evaluated": 0, "wins": 0, "average_return_pct": 0.0})
        asset_count = int(asset.get("evaluated") or 0)
        asset_avg = float(asset.get("average_return_pct") or 0.0)
        asset["evaluated"] = asset_count + 1
        asset["average_return_pct"] = round((asset_avg * asset_count + return_pct) / (asset_count + 1), 4)
        if outcome > 0:
            asset["wins"] = int(asset.get("wins") or 0) + 1

        item["learned_24h"] = True
        item["learned_at"] = now.isoformat(timespec="seconds")
        learned += 1
    state["updated_at"] = now.isoformat(timespec="seconds")
    return learned
