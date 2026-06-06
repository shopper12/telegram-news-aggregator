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
    hours: int = 6
    limit: int = 15
    briefing_kind: str = "regular"


def _require_api_key(x_api_key: str | None) -> None:
    expected = os.getenv("NEWS_BOT_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _report_text() -> str:
    data = load_latest_report()
    return str(data.get("report") or "최신 뉴스 리포트가 없습니다.")


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "service": "telegram_news_bot_api",
        "endpoints": [
            "/health",
            "/api/news",
            "/api/news.txt",
            "/api/refresh",
            "/api/kakao-skill",
            "/docs",
        ],
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "telegram_news_bot_api"}


@app.get("/api/news")
def get_news(x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    return load_latest_report()


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
    return load_latest_report()


@app.post("/api/kakao-skill")
async def kakao_skill(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    payload = await request.json()
    utterance = str(
        payload.get("userRequest", {}).get("utterance")
        or payload.get("utterance")
        or ""
    ).strip()
    if utterance and "뉴스" not in utterance:
        text = "'뉴스'라고 입력하면 현재 기준 중요 뉴스 요약을 알려드립니다."
    else:
        text = _report_text()
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": text[:990]
                    }
                }
            ]
        },
    }
