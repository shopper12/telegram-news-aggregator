from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os

import requests

LATEST_REPORT_JSON = Path("reports/latest_report.json")
LATEST_REPORT_MD = Path("reports/latest_report.md")
DEFAULT_LATEST_REPORT_URL = "https://raw.githubusercontent.com/shopper12/telegram-news-aggregator/main/reports/latest_report.json"
FALLBACK_TIMEOUT_SECONDS = float(os.getenv("LATEST_REPORT_FALLBACK_TIMEOUT_SECONDS", "5.0"))
MAX_CACHE_AGE_SECONDS = int(os.getenv("REPORT_CACHE_MAX_AGE_SECONDS", "3600"))  # 기본 1시간
MIN_REPORT_OK_LENGTH = int(os.getenv("MIN_REPORT_OK_LENGTH", "100"))


def _normalize_report_payload(data: dict) -> dict:
    """ok=False라도 충분한 report 본문이 있으면 메신저 출력용으로 유효 처리한다."""
    report_str = str(data.get("report", "")).strip()
    if len(report_str) >= MIN_REPORT_OK_LENGTH:
        data["ok"] = True
        data.setdefault("recovered_ok_reason", "report_body_present")
    return data


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
    separator = "&" if "?" in url else "?"
    url = url.rstrip("?&") + f"{separator}t={int(datetime.now().timestamp())}"
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
            return _normalize_report_payload(data)
    except Exception:
        return None
    return None


def _age_seconds(generated_at: str) -> int | None:
    try:
        return int((datetime.now() - datetime.fromisoformat(generated_at)).total_seconds())
    except Exception:
        return None


def _is_stale(generated_at: str) -> bool:
    age = _age_seconds(generated_at)
    return bool(age is not None and age > MAX_CACHE_AGE_SECONDS)


def _with_stale_notice(data: dict) -> dict:
    generated_at = data.get("generated_at", "")
    age_sec = _age_seconds(generated_at) if generated_at else None
    if age_sec is None:
        data["stale"] = True
        return _normalize_report_payload(data)
    age_h = age_sec // 3600
    age_m = (age_sec % 3600) // 60
    stale_notice = f"⚠️ 마지막 업데이트로부터 {age_h}시간 {age_m}분 경과\n\n"
    data["stale"] = True
    data["report"] = stale_notice + str(data.get("report", ""))
    return _normalize_report_payload(data)


def load_latest_report() -> dict:
    if LATEST_REPORT_JSON.exists():
        try:
            data = _normalize_report_payload(json.loads(LATEST_REPORT_JSON.read_text(encoding="utf-8")))
            generated_at = data.get("generated_at", "")
            if generated_at and _is_stale(generated_at):
                fallback = _load_github_fallback()
                if fallback:
                    fallback["fallback_reason"] = "local_cache_stale"
                    return fallback
                return _with_stale_notice(data)
            return data
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
