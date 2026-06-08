from __future__ import annotations

import os

from fastapi import Header, HTTPException
from pydantic import BaseModel

from .api_server import app, _report_data, _report_text
from .bot_services_v5 import handle_command


class BotCommandRequest(BaseModel):
    message: str
    user_id: str = "default"


def _require_api_key_ext(x_api_key: str | None) -> None:
    expected = os.getenv("NEWS_BOT_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


@app.post("/api/bot-command")
def post_bot_command(req: BotCommandRequest, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key_ext(x_api_key)
    data = _report_data()
    latest = str(data.get("report") or "뉴스 없음").strip() or "뉴스 없음"
    reply = handle_command(user_id=req.user_id or "default", message=req.message or "", latest_report=latest)
    return {"ok": True, "message": reply, "kind": data.get("kind"), "hours": data.get("hours"), "source": data.get("source"), "generated_at": data.get("generated_at")}


@app.get("/api/bot-help")
def bot_help(x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key_ext(x_api_key)
    return {"ok": True, "message": handle_command(user_id="help", message="봇 도움말", latest_report=_report_text())}
