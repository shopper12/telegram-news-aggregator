from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .report_cache import load_latest_report

app = FastAPI(title="Telegram News Aggregator Bot API")


class RefreshRequest(BaseModel):
    hours: int = 1
    limit: int = 999
    briefing_kind: str = "regular"


def _require_api_key(x_api_key: str | None) -> None:
    expected = os.getenv("NEWS_BOT_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _report_data() -> dict:
    return load_latest_report()


def _report_text() -> str:
    data = _report_data()
    return str(data.get("report") or "최신 뉴스 리포트가 없습니다.")


def _bot_message_payload() -> dict:
    data = _report_data()
    message = str(data.get("report") or "뉴스 없음").strip() or "뉴스 없음"
    return {
        "ok": bool(data.get("ok", False)),
        "message": message,
        "kind": data.get("kind"),
        "hours": data.get("hours"),
        "source": data.get("source"),
        "generated_at": data.get("generated_at"),
        "fallback_reason": data.get("fallback_reason"),
    }


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "service": "telegram_news_bot_api",
        "endpoints": [
            "/health",
            "/api/news",
            "/api/news.txt",
            "/api/news-message",
            "/api/refresh",
            "/api/kakao-skill",
            "/skill",
            "/docs",
        ],
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "telegram_news_bot_api"}


@app.get("/api/news")
def get_news(x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    return _report_data()


@app.get("/api/news-message")
def get_news_message(x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    return _bot_message_payload()


@app.get("/api/news.txt", response_class=PlainTextResponse)
def get_news_text(x_api_key: str | None = Header(default=None)) -> str:
    _require_api_key(x_api_key)
    return _report_text()


@app.post("/api/refresh")
def refresh_news(req: RefreshRequest, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    env = os.environ.copy()
    env["BRIEFING_KIND"] = req.briefing_kind
    cmd = [
        sys.executable,
        "scripts/run_once.py",
        "run",
        "--hours",
        str(req.hours),
        "--limit",
        str(req.limit),
    ]
    completed = subprocess.run(cmd, cwd=Path.cwd(), env=env, text=True, capture_output=True, timeout=900)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "refresh_failed",
                "stdout": completed.stdout[-3000:],
                "stderr": completed.stderr[-3000:],
            },
        )
    return _report_data()


def _extract_utterance(payload: dict) -> str:
    return str(
        payload.get("userRequest", {}).get("utterance")
        or payload.get("utterance")
        or payload.get("action", {}).get("params", {}).get("utterance")
        or ""
    ).strip()


def _extract_user_id(payload: dict) -> str:
    user = payload.get("userRequest", {}).get("user") or {}
    props = user.get("properties") or {}
    for key in ["plusfriendUserKey", "appUserId", "botUserKey"]:
        value = props.get(key) or user.get(key)
        if value:
            return str(value)
    return "kakao-default"


def _kakao_simple_text(text: str) -> dict:
    value = str(text or "뉴스 없음").strip() or "뉴스 없음"
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": value[:990]
                    }
                }
            ]
        },
    }


def _skill_answer(utterance: str, user_id: str = "kakao-default") -> str:
    text = str(utterance or "").strip()
    if not text:
        text = "봇 도움말"
    if not text.startswith("봇"):
        text = "봇 " + text
    try:
        from .bot_services_v7 import handle_command
    except Exception:
        from .bot_services_v5 import handle_command
    latest = _report_text()
    return handle_command(user_id=user_id, message=text, latest_report=latest)


async def _handle_kakao_skill(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    utterance = _extract_utterance(payload)
    user_id = _extract_user_id(payload)
    return _kakao_simple_text(_skill_answer(utterance, user_id))


@app.post("/api/kakao-skill")
async def kakao_skill(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
    return await _handle_kakao_skill(request, x_api_key)


@app.post("/skill")
async def skill(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
    return await _handle_kakao_skill(request, x_api_key)
