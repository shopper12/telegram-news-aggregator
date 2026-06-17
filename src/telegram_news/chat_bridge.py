from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from fastapi import Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

VERSION = "chat-picks-bridge-v1"
DEFAULT_PATH = "/tmp/chat_picks.json"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _need_key(x_api_key: str | None) -> None:
    expected = os.getenv("CHAT_PICKS_API_KEY") or os.getenv("NEWS_BOT_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _json_from_text(text: str) -> dict[str, Any] | None:
    value = _clean(text)
    if not value:
        return None
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    first = value.find("{")
    last = value.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(value[first : last + 1])
        except Exception:
            return None
    return None


def _raw_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ["recommendations", "active_recommendations", "items"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    for key in ["briefing_state", "briefing_state_json", "state"]:
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _raw_items(nested)
            if found:
                return found
    text = payload.get("text")
    if isinstance(text, str):
        parsed = _json_from_text(text)
        if parsed:
            return _raw_items(parsed)
    return []


def _item(row: dict[str, Any]) -> dict[str, Any] | None:
    name = _clean(row.get("asset_name") or row.get("name") or row.get("asset") or row.get("종목") or row.get("자산"))
    ticker = _clean(row.get("ticker") or row.get("symbol") or row.get("code") or row.get("종목코드"))
    if not name and not ticker:
        return None
    return {
        "asset_name": name,
        "ticker": ticker,
        "market": _clean(row.get("market") or row.get("시장")),
        "direction": _clean(row.get("direction") or row.get("방향") or "watch"),
        "basis_price": row.get("basis_price") or row.get("current_price") or row.get("reference_price") or row.get("기준가"),
        "entry": _clean(row.get("entry") or row.get("진입") or row.get("entry_range")),
        "stop": _clean(row.get("stop") or row.get("손절") or row.get("stop_loss")),
        "target1": _clean(row.get("target1") or row.get("목표1") or row.get("tp1")),
        "target2": _clean(row.get("target2") or row.get("목표2") or row.get("tp2")),
        "memo": _clean(row.get("reason") or row.get("근거") or row.get("why_now") or row.get("source_note")),
    }


def _normalise(payload: dict[str, Any]) -> dict[str, Any]:
    items = [_item(x) for x in _raw_items(payload)]
    items = [x for x in items if x]
    return {
        "ok": True,
        "version": VERSION,
        "source": _clean(payload.get("source") or "chatgpt_market_briefing"),
        "briefing_datetime_kst": _clean(payload.get("briefing_datetime_kst") or payload.get("timestamp_kst")),
        "mode": _clean(payload.get("mode")),
        "recommendations": items,
        "count": len(items),
    }


def _path() -> Path:
    return Path(os.getenv("CHAT_PICKS_PATH", DEFAULT_PATH))


def _read_local() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"ok": True, "version": VERSION, "recommendations": [], "count": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("recommendations", [])
            data["count"] = len(data.get("recommendations") or [])
            return data
    except Exception as exc:
        return {"ok": False, "version": VERSION, "error": f"load_failed:{type(exc).__name__}", "recommendations": [], "count": 0}
    return {"ok": True, "version": VERSION, "recommendations": [], "count": 0}


def _write_local(payload: dict[str, Any]) -> dict[str, Any]:
    data = _normalise(payload)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _load() -> dict[str, Any]:
    url = os.getenv("CHAT_PICKS_SOURCE_URL") or os.getenv("STOCK_SCANNER_RECOMMENDATIONS_URL")
    if url:
        try:
            res = requests.get(url, timeout=4.0, headers={"User-Agent": "chat-picks-bridge/1.0"})
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict):
                    return _normalise(data)
        except Exception:
            pass
    env_json = os.getenv("CHAT_PICKS_JSON")
    if env_json:
        try:
            return _normalise(json.loads(env_json))
        except Exception:
            pass
    return _read_local()


def _text(data: dict[str, Any]) -> str:
    rows = data.get("recommendations") or []
    if not rows:
        return "저장된 추천종목이 없습니다. 브리핑 JSON을 /api/recommendations 로 먼저 보내세요."
    when = data.get("briefing_datetime_kst") or data.get("generated_at") or "시간미상"
    lines = ["📌 추천종목", f"기준: {when}", ""]
    for idx, row in enumerate(rows[:5], 1):
        name = row.get("asset_name") or row.get("ticker") or "미상"
        ticker = row.get("ticker") or ""
        direction = row.get("direction") or "watch"
        lines.append(f"{idx}) {name}{f'({ticker})' if ticker else ''} / {direction}")
        price = row.get("basis_price")
        if price not in (None, ""):
            lines.append(f"기준가: {price}")
        if row.get("entry"):
            lines.append(f"진입: {row['entry']}")
        if row.get("stop") or row.get("target1") or row.get("target2"):
            lines.append(f"손절/목표: {row.get('stop') or '-'} / {row.get('target1') or '-'} / {row.get('target2') or '-'}")
        if row.get("memo"):
            lines.append(f"근거: {str(row['memo'])[:100]}")
        lines.append("")
    return "\n".join(lines).strip()[:1400]


async def _payload(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
        if isinstance(data, dict):
            return data
        return {"recommendations": data}
    except Exception:
        raw = (await request.body()).decode("utf-8", errors="ignore")
        return _json_from_text(raw) or {"text": raw}


def _is_pick_command(message: str, strip_bot) -> bool:
    body = strip_bot(message)
    compact = re.sub(r"\s+", "", _clean(body).lower())
    return compact in {"추천", "추천종목", "추천주", "종목추천", "오늘추천", "매매추천", "gpt추천", "chatgpt추천", "recommend", "recommendations", "recs"}


def apply(api_module: Any) -> Any:
    app = getattr(api_module, "app")
    if getattr(app.state, "chat_picks_bridge_registered", False):
        return api_module
    app.state.chat_picks_bridge_registered = True

    @app.get("/api/recommendations")
    def get_recommendations() -> dict[str, Any]:
        return _load()

    @app.get("/api/recommendations.txt", response_class=PlainTextResponse)
    def get_recommendations_text() -> str:
        return _text(_load())

    @app.post("/api/recommendations")
    async def post_recommendations(request: Request, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _need_key(x_api_key)
        return _write_local(await _payload(request))

    original_answer = getattr(api_module, "answer", None)
    strip_bot = getattr(api_module, "_strip_bot", lambda x: x)
    if callable(original_answer):
        def patched_answer(message: str, user_id: str) -> str:
            if _is_pick_command(message, strip_bot):
                return _text(_load())
            return original_answer(message, user_id)
        api_module.answer = patched_answer
    return api_module
