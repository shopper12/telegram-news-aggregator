from datetime import datetime
import json

import pytest

from telegram_news import kakao_notifier, report_cache


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_stale_local_report_prefers_newer_github_report(tmp_path, monkeypatch):
    local_path = tmp_path / "latest_report.json"
    local_path.write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at": "2026-07-22T03:59:21",
                "report": "7월 22일 오래된 뉴스 " + ("x" * 120),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fresh_time = datetime.now().isoformat(timespec="seconds")
    remote_report = "8월 최신 뉴스 " + ("y" * 120)

    monkeypatch.setattr(report_cache, "LATEST_REPORT_JSON", local_path)
    monkeypatch.setattr(report_cache, "ENABLE_GITHUB_REPORT_FALLBACK", True)
    monkeypatch.setattr(
        report_cache.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            200,
            {
                "ok": True,
                "generated_at": fresh_time,
                "report": remote_report,
            },
        ),
    )

    result = report_cache.load_latest_report()

    assert result["report"] == remote_report
    assert result["fallback_reason"] == "local_cache_stale_github_newer"


def test_kakao_oauth_400_exposes_error_without_secret(monkeypatch):
    monkeypatch.setattr(
        kakao_notifier.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(
            400,
            {
                "error": "invalid_grant",
                "error_description": "refresh token is invalid or expired",
            },
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        kakao_notifier.refresh_kakao_access_token(
            rest_api_key="rest-key",
            refresh_token="refresh-secret",
        )

    message = str(exc_info.value)
    assert "HTTP 400" in message
    assert "invalid_grant" in message
    assert "refresh token is invalid or expired" in message
    assert "refresh-secret" not in message
