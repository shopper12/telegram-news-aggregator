from __future__ import annotations

import contextvars
import os
import re
from pathlib import Path
from typing import Any
from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .service import CrosswordService

_CURRENT_KAKAO_USER: contextvars.ContextVar[str | None] = contextvars.ContextVar("crossword_kakao_user", default=None)

class AnswersBody(BaseModel): answers: dict[str, str]
class NicknameBody(BaseModel): nickname: str
class HintBody(BaseModel): puzzleId: str; clueId: str
class HintAnswerBody(BaseModel): hintText: str

def _extract_kakao_user(payload: dict) -> str | None:
    user = payload.get("userRequest", {}).get("user") if isinstance(payload.get("userRequest"), dict) else None
    user = user if isinstance(user, dict) else {}
    props = user.get("properties") if isinstance(user.get("properties"), dict) else {}
    for key in ("plusfriendUserKey", "appUserId", "botUserKey"):
        value = props.get(key) or user.get(key)
        if value: return "kakao:" + str(value)
    return None

def install(api_module: Any) -> Any:
    if getattr(api_module, "_CROSSWORD_INSTALLED", False): return api_module
    service = CrosswordService(); app = api_module.app; static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/crossword/static", StaticFiles(directory=static_dir), name="crossword-static")
    original_payload = api_module._payload
    async def payload_with_user(request: Request) -> dict:
        data = await original_payload(request); _CURRENT_KAKAO_USER.set(_extract_kakao_user(data)); return data
    api_module._payload = payload_with_user
    original_help = api_module._help
    def help_with_crossword() -> str:
        text = original_help(); return text if "크로스워드" in text else text + "\n봇 크로스워드 - 오늘의 한글/영어 크로스워드"
    api_module._help = help_with_crossword
    original_answer = api_module.answer
    def answer_with_crossword(message: str, user_id: str) -> str:
        compact = re.sub(r"\s+", "", api_module._strip_bot(message)).lower()
        if compact in {"크로스워드", "게임", "오늘의게임", "퍼즐", "crossword"}:
            effective = _CURRENT_KAKAO_USER.get() or user_id or "default-user"
            _key, session = service.session_for_platform_user(effective); url = service.game_url(session, "ko")
            return f"🧩 오늘의 크로스워드\n🇰🇷 한글 / 🇺🇸 English 중 선택해서 플레이하세요.\n🏆 친구 랭킹 · 💡 친구 힌트 지원\n{url}"
        return original_answer(message, user_id)
    api_module.answer = answer_with_crossword
    original_kakao = api_module._kakao
    def kakao_with_crossword(text: str) -> dict:
        if text.startswith("🧩 오늘의 크로스워드"):
            match = re.search(r"https?://\S+", text); ko_url = match.group(0) if match else service.public_base_url + "/crossword"; en_url = re.sub(r"language=ko", "language=en", ko_url)
            return {"version":"2.0","template":{"outputs":[{"basicCard":{"title":"🧩 오늘의 크로스워드","description":"매일 새 문제 1개 · 단일 난이도 · 친구 랭킹 · 친구 힌트","buttons":[{"action":"webLink","label":"🇰🇷 한글 게임","webLinkUrl":ko_url},{"action":"webLink","label":"🇺🇸 English","webLinkUrl":en_url}]}}]}}
        return original_kakao(text)
    api_module._kakao = kakao_with_crossword
    def auth_user(authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "): raise HTTPException(status_code=401, detail="LOGIN_REQUIRED")
        try: return service.verify_session(authorization.split(" ",1)[1].strip())
        except ValueError: raise HTTPException(status_code=401, detail="INVALID_SESSION")
    @app.get("/crossword", include_in_schema=False)
    def crossword_page() -> FileResponse: return FileResponse(static_dir / "index.html")
    @app.get("/api/crossword/config")
    def config() -> dict: return {"kakaoJavaScriptKey":service.kakao_js_key,"sdkVersion":"2.8.2"}
    @app.post("/api/crossword/session/guest")
    def guest() -> dict:
        key, token = service.guest_session(); return {"token":token,"nickname":service.default_nickname(key)}
    @app.get("/api/crossword/today")
    def today(language: str="ko") -> dict: return service.today(language)
    @app.post("/api/crossword/plays/{puzzle_id}/start")
    def start(puzzle_id:str, authorization:str|None=Header(default=None))->dict: return service.start(auth_user(authorization),puzzle_id)
    @app.post("/api/crossword/plays/{puzzle_id}/submit")
    def submit(puzzle_id:str, body:AnswersBody, authorization:str|None=Header(default=None))->dict:
        try:return service.submit(auth_user(authorization),puzzle_id,body.answers)
        except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc))
    @app.post("/api/crossword/me/nickname")
    def nickname(body:NicknameBody,authorization:str|None=Header(default=None))->dict:
        user=auth_user(authorization);name=body.nickname.strip()[:20]
        if len(name)<2:raise HTTPException(status_code=400,detail="INVALID_NICKNAME")
        service.store.set_nickname(user,name);return {"ok":True,"nickname":name}
    @app.post("/api/crossword/friends/invite")
    def invite(authorization:str|None=Header(default=None))->dict:return {"shareUrl":service.create_friend_invite(auth_user(authorization))}
    @app.post("/api/crossword/friends/invite/{token}/accept")
    def accept(token:str,authorization:str|None=Header(default=None))->dict:
        try:friend=service.accept_friend_invite(token,auth_user(authorization));return {"ok":True,"friendKey":friend[:8]}
        except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    @app.get("/api/crossword/leaderboard")
    def leaderboard(language:str="ko",authorization:str|None=Header(default=None))->dict:
        user=auth_user(authorization);puzzle=service.today(language);return {"players":service.store.leaderboard(user,puzzle["puzzle_id"])}
    @app.post("/api/crossword/hints")
    def create_hint(body:HintBody,authorization:str|None=Header(default=None))->dict:
        try:return service.create_hint_request(auth_user(authorization),body.puzzleId,body.clueId)
        except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    @app.get("/api/crossword/hints/help/{token}")
    def hint_help(token:str)->dict:
        try:return service.hint_help(token)
        except ValueError as exc:raise HTTPException(status_code=404,detail=str(exc))
    @app.post("/api/crossword/hints/help/{token}")
    def hint_answer(token:str,body:HintAnswerBody,authorization:str|None=Header(default=None))->dict:
        try:return {"ok":True,"requestId":service.answer_hint(token,auth_user(authorization),body.hintText)}
        except ValueError as exc:raise HTTPException(status_code=403,detail=str(exc))
    @app.get("/api/crossword/hints/{request_id}")
    def hint_requester(request_id:str,authorization:str|None=Header(default=None))->dict:
        try:return service.requester_hint(request_id,auth_user(authorization))
        except ValueError as exc:raise HTTPException(status_code=404,detail=str(exc))
    @app.post("/api/crossword/admin/publish")
    def publish(authorization:str|None=Header(default=None))->dict:
        expected=os.getenv("CROSSWORD_ADMIN_TOKEN","")
        if not expected or authorization!=f"Bearer {expected}":raise HTTPException(status_code=401,detail="INVALID_ADMIN_TOKEN")
        return service.broadcast_daily()
    api_module._CROSSWORD_INSTALLED=True;api_module.CROSSWORD_SERVICE=service;return api_module
