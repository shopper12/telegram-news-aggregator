from __future__ import annotations

import base64
from datetime import datetime
import json
from types import SimpleNamespace

from telegram_news import report_cache, saju_news_patch


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_saju_patch_does_not_replace_canonical_news_handler():
    original_news = lambda: "canonical market note"

    api = SimpleNamespace(
        _news=original_news,
        _live_news=lambda: "live",
        answer=lambda message, user_id: original_news() if "뉴스" in message else "ok",
        _strip_bot=lambda value: str(value).replace("봇 ", "", 1),
    )

    saju_news_patch.apply(api)

    assert api._news is original_news
    assert api._live_news() == "live"
    assert api.answer("봇 뉴스", "user") == "canonical market note"
    assert api.API_VERSION == "messenger-telegram-source-v7"


def test_stale_local_report_recovers_through_contents_api_when_raw_fails(tmp_path, monkeypatch):
    local_path = tmp_path / "latest_report.json"
    old_report = "오래된 Render 뉴스 " + ("x" * 120)
    local_path.write_text(
        json.dumps(
            {
                "ok": True,
                "generated_at": "2026-08-07T15:01:02",
                "report": old_report,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fresh_report = "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n최신 GitHub 시장 노트\n" + ("y" * 120)
    fresh_payload = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "report": fresh_report,
    }
    encoded = base64.b64encode(json.dumps(fresh_payload, ensure_ascii=False).encode("utf-8")).decode("ascii")

    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "raw.githubusercontent.com" in url:
            return FakeResponse(503, text="raw unavailable")
        if "api.github.com/repos/" in url:
            return FakeResponse(200, {"content": encoded, "encoding": "base64"})
        raise AssertionError(url)

    monkeypatch.setattr(report_cache, "LATEST_REPORT_JSON", local_path)
    monkeypatch.setattr(report_cache, "ENABLE_GITHUB_REPORT_FALLBACK", True)
    monkeypatch.setattr(report_cache, "ALLOW_STALE_GITHUB_FALLBACK", False)
    monkeypatch.setattr(report_cache, "ALLOW_STALE_LOCAL_REPORT", False)
    monkeypatch.setattr(report_cache.requests, "get", fake_get)

    result = report_cache.load_latest_report()

    assert result["report"] == fresh_report
    assert result["remote_path"] == "contents_api"
    assert result["fallback_reason"] == "local_cache_stale_github_newer"
    assert len(calls) == 2
