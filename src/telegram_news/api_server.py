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


def _skill_answer(utterance: str) -> str:
    q = str(utterance or "").strip().lower()

    if not q or "도움" in q or q in {"?", "help", "/help"}:
        return (
            "사용 가능한 명령어\n"
            "뉴스 - 최신 투자 뉴스\n"
            "시황 - 최신 시장 뉴스\n"
            "도움말 - 명령어 안내"
        )

    if any(word in q for word in ["뉴스", "주식", "시황", "브리핑", "시장", "news"]):
        return _report_text()

    return "명령어를 인식하지 못했습니다. '뉴스' 또는 '도움말'을 입력하세요."


async def _handle_kakao_skill(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    utterance = _extract_utterance(payload)
    return _kakao_simple_text(_skill_answer(utterance))


@app.post("/api/kakao-skill")
async def kakao_skill(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
    return await _handle_kakao_skill(request, x_api_key)


@app.post("/skill")
async def skill(request: Request, x_api_key: str | None = Header(default=None)) -> dict:
    return await _handle_kakao_skill(request, x_api_key)
