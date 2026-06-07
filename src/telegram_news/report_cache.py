from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os

import requests

LATEST_REPORT_JSON = Path("reports/latest_report.json")
LATEST_REPORT_MD = Path("reports/latest_report.md")
DEFAULT_LATEST_REPORT_URL = "https://raw.githubusercontent.com/shopper12/telegram-news-aggregator/main/reports/latest_report.json"
FALLBACK_TIMEOUT_SECONDS = float(os.getenv("LATEST_REPORT_FALLBACK_TIMEOUT_SECONDS", "2.0"))


def save_latest_report(*, report: str, kind: str, hours: int, source: str = "scheduled") -> None:
    LATEST_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().isoformat(timespec="seconds")
    LATEST_REPORT_MD.write_text(report, encoding="utf-8")
    LATEST_REPORT_JSON.write_text(
        json.dumps(
            {
                "ok": True,
                "kind": kind,
                "hours": hours,
                "source": source,
                "generated_at": generated_at,
                "report": report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_github_fallback() -> dict | None:
    url = os.getenv("LATEST_REPORT_URL", DEFAULT_LATEST_REPORT_URL).strip()
    if not url:
        return None
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(url, headers=headers, timeout=FALLBACK_TIMEOUT_SECONDS)
        if response.status_code != 200:
            return None
        data = response.json()
        if isinstance(data, dict) and data.get("report"):
            data.setdefault("source", "github_fallback")
            return data
    except Exception:
        return None
    return None


def load_latest_report() -> dict:
    if LATEST_REPORT_JSON.exists():
        try:
            return json.loads(LATEST_REPORT_JSON.read_text(encoding="utf-8"))
        except Exception as exc:
            fallback = _load_github_fallback()
            if fallback:
                fallback["fallback_reason"] = f"local_read_failed:{type(exc).__name__}"
                return fallback
            return {
                "ok": False,
                "error": "latest_report_read_failed",
                "detail": f"{type(exc).__name__}: {exc}",
                "report": "최신 뉴스 리포트를 읽지 못했습니다.",
            }

    fallback = _load_github_fallback()
    if fallback:
        fallback["fallback_reason"] = "local_latest_report_not_found"
        return fallback
    return {
        "ok": False,
        "error": "latest_report_not_found",
        "report": "아직 생성된 뉴스 리포트가 없습니다. 정시 분석 또는 /api/refresh 실행이 먼저 필요합니다.",
    }
