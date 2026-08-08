from __future__ import annotations

from datetime import datetime
from pathlib import Path
import base64
import json
import os

import requests

LATEST_REPORT_JSON = Path("reports/latest_report.json")
LATEST_REPORT_MD = Path("reports/latest_report.md")
DEFAULT_LATEST_REPORT_URL = "https://raw.githubusercontent.com/shopper12/telegram-news-aggregator/main/reports/latest_report.json"
DEFAULT_LATEST_REPORT_API_URL = "https://api.github.com/repos/shopper12/telegram-news-aggregator/contents/reports/latest_report.json?ref=main"
FALLBACK_TIMEOUT_SECONDS = float(os.getenv("LATEST_REPORT_FALLBACK_TIMEOUT_SECONDS", "5.0"))
MAX_CACHE_AGE_SECONDS = int(os.getenv("REPORT_CACHE_MAX_AGE_SECONDS", "3600"))  # 기본 1시간
MIN_REPORT_OK_LENGTH = int(os.getenv("MIN_REPORT_OK_LENGTH", "100"))
# Render is long-lived while GitHub Actions keeps reports/latest_report.json fresh.
# Enable the GitHub source by default, but accept it only when it is newer and not stale.
ENABLE_GITHUB_REPORT_FALLBACK = os.getenv("ENABLE_GITHUB_REPORT_FALLBACK", "1") == "1"
ALLOW_STALE_GITHUB_FALLBACK = os.getenv("ALLOW_STALE_GITHUB_FALLBACK", "0") == "1"
ALLOW_STALE_LOCAL_REPORT = os.getenv("ALLOW_STALE_LOCAL_REPORT", "0") == "1"


def _normalize_report_payload(data: dict) -> dict:
    report_str = str(data.get("report", "")).strip()
    if len(report_str) >= MIN_REPORT_OK_LENGTH:
        data["ok"] = True
        data.setdefault("recovered_ok_reason", "report_body_present")
    return data


def _parse_generated_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _is_newer_report(candidate: dict | None, current: dict | None) -> bool:
    if not candidate:
        return False
    candidate_dt = _parse_generated_at(str(candidate.get("generated_at") or ""))
    current_dt = _parse_generated_at(str((current or {}).get("generated_at") or ""))
    if candidate_dt and current_dt:
        return candidate_dt > current_dt
    return bool(candidate_dt and not current_dt)


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


def _age_seconds(generated_at: str) -> int | None:
    dt = _parse_generated_at(generated_at)
    if not dt:
        return None
    try:
        return int((datetime.now() - dt).total_seconds())
    except Exception:
        return None


def _is_stale(generated_at: str) -> bool:
    age = _age_seconds(generated_at)
    return bool(age is not None and age > MAX_CACHE_AGE_SECONDS)


def _validated_remote_payload(data: dict | None, source: str) -> dict | None:
    if not isinstance(data, dict) or not str(data.get("report") or "").strip():
        return None
    generated_at = str(data.get("generated_at") or "")
    if generated_at and _is_stale(generated_at) and not ALLOW_STALE_GITHUB_FALLBACK:
        return None
    data = dict(data)
    data.setdefault("source", source)
    return _normalize_report_payload(data)


def _decode_contents_api_payload(payload: dict) -> dict | None:
    encoded = str(payload.get("content") or "").replace("\n", "").strip()
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded).decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_github_fallback() -> dict | None:
    """Load the newest persisted report through two independent GitHub paths.

    Render occasionally fails a raw.githubusercontent.com request. A single raw
    failure must not make Kakao fall back to a stale local checkout, so the
    GitHub Contents API is used as a second source before stale data is returned.
    """
    if not ENABLE_GITHUB_REPORT_FALLBACK:
        return None

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    auth_headers = {"Accept": "application/json", "User-Agent": "telegram-news-report-cache/2.0"}
    if token:
        auth_headers["Authorization"] = f"Bearer {token}"

    raw_url = os.getenv("LATEST_REPORT_URL", DEFAULT_LATEST_REPORT_URL).strip()
    if raw_url:
        separator = "&" if "?" in raw_url else "?"
        raw_url = raw_url.rstrip("?&") + f"{separator}t={int(datetime.now().timestamp())}"
        try:
            response = requests.get(raw_url, headers=auth_headers, timeout=FALLBACK_TIMEOUT_SECONDS)
            if response.status_code == 200:
                candidate = _validated_remote_payload(response.json(), "github_raw")
                if candidate:
                    candidate["remote_path"] = "raw"
                    return candidate
        except Exception:
            pass

    api_url = os.getenv("LATEST_REPORT_API_URL", DEFAULT_LATEST_REPORT_API_URL).strip()
    if api_url:
        separator = "&" if "?" in api_url else "?"
        api_url = api_url.rstrip("?&") + f"{separator}t={int(datetime.now().timestamp())}"
        api_headers = dict(auth_headers)
        api_headers["Accept"] = "application/vnd.github+json"
        try:
            response = requests.get(api_url, headers=api_headers, timeout=FALLBACK_TIMEOUT_SECONDS)
            if response.status_code == 200:
                payload = response.json()
                decoded = _decode_contents_api_payload(payload) if isinstance(payload, dict) else None
                candidate = _validated_remote_payload(decoded, "github_contents_api")
                if candidate:
                    candidate["remote_path"] = "contents_api"
                    return candidate
        except Exception:
            pass

    return None


def _stale_result(data: dict) -> dict:
    generated_at = str(data.get("generated_at") or "시간미상")
    age_sec = _age_seconds(generated_at) if generated_at != "시간미상" else None
    if ALLOW_STALE_LOCAL_REPORT:
        report = str(data.get("report") or "")
        if not report.startswith("⚠️ 마지막 업데이트"):
            if age_sec is None:
                notice = "⚠️ 오래된 저장 리포트입니다.\n\n"
            else:
                age_h = age_sec // 3600
                age_m = (age_sec % 3600) // 60
                notice = f"⚠️ 마지막 업데이트로부터 {age_h}시간 {age_m}분 경과\n\n"
            data["report"] = notice + report
        data["stale"] = True
        return _normalize_report_payload(data)

    return {
        "ok": False,
        "stale": True,
        "error": "latest_report_stale",
        "generated_at": generated_at,
        "report": (
            "⚠️ 최신 뉴스 리포트가 아직 갱신되지 않았습니다.\n"
            f"마지막 저장 시각: {generated_at}\n"
            "오래된 뉴스 본문은 표시하지 않습니다. GitHub Actions 최신 수집 결과를 확인하세요."
        ),
    }


def load_latest_report() -> dict:
    if LATEST_REPORT_JSON.exists():
        try:
            data = _normalize_report_payload(json.loads(LATEST_REPORT_JSON.read_text(encoding="utf-8")))
            generated_at = str(data.get("generated_at") or "")
            report = str(data.get("report") or "").strip()

            if generated_at and _is_stale(generated_at):
                fallback = _load_github_fallback()
                if fallback and _is_newer_report(fallback, data):
                    fallback["fallback_reason"] = "local_cache_stale_github_newer"
                    return fallback
                return _stale_result(data)

            if len(report) < MIN_REPORT_OK_LENGTH:
                fallback = _load_github_fallback()
                if fallback:
                    fallback["fallback_reason"] = "local_report_too_short"
                    return fallback
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
                "report": "최신 텔레그램 뉴스 리포트를 읽지 못했습니다. /api/refresh 또는 정시 수집이 필요합니다.",
            }

    fallback = _load_github_fallback()
    if fallback:
        fallback["fallback_reason"] = "local_latest_report_not_found"
        return fallback
    return {
        "ok": False,
        "error": "latest_report_not_found",
        "report": "아직 생성된 최신 뉴스 리포트가 없습니다. /api/refresh 또는 정시 수집이 먼저 필요합니다.",
    }
