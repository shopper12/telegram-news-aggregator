from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests


def _items(payload: dict[str, Any]) -> list[Any]:
    for key in ["recommendations", "active_recommendations", "items"]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    nested = payload.get("briefing_state") or payload.get("briefing_state_json") or payload.get("state")
    if isinstance(nested, dict):
        return _items(nested)
    return []


def _target_url() -> str:
    explicit = os.getenv("CHAT_PICKS_POST_URL")
    if explicit:
        return explicit.strip()
    base = os.getenv("RENDER_API_BASE_URL")
    if base:
        return base.rstrip("/") + "/api/recommendations"
    return ""


def main() -> int:
    path = Path(os.getenv("APP_RECOMMENDATIONS_FILE", "reports/app_recommendations.json"))
    if not path.exists():
        print(f"skip: missing file {path}")
        return 0

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("payload must be a JSON object")

    items = _items(payload)
    if not items:
        print("skip: no recommendation items")
        return 0

    url = _target_url()
    if not url:
        raise SystemExit("CHAT_PICKS_POST_URL or RENDER_API_BASE_URL is required")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "chatgpt-market-briefing-publisher/1.0",
    }
    api_key = os.getenv("CHAT_PICKS_API_KEY") or os.getenv("NEWS_BOT_API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key

    response = requests.post(url, json=payload, headers=headers, timeout=20)
    print(f"post status={response.status_code} url={url} items={len(items)}")
    if response.text:
        print(response.text[:1000])
    response.raise_for_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
