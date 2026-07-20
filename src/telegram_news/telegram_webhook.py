from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import BackgroundTasks, HTTPException, Request

from .notifier import send_telegram_message
from .telegram_dispatch import generate_and_send_latest_report


DEFAULT_PUBLIC_BASE_URL = "https://telegram-news-bot-api.onrender.com"
WEBHOOK_PATH = "/telegram/webhook"
REFRESH_COMMANDS = {
    "뉴스갱신",
    "/뉴스갱신",
    "뉴스새로고침",
    "새로고침",
    "뉴스업데이트",
    "새뉴스",
    "refresh",
    "뉴스refresh",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _bot_token() -> str:
    return _clean(os.getenv("TELEGRAM_BOT_TOKEN")).strip('"\'')


def _webhook_secret() -> str:
    return _clean(os.getenv("TELEGRAM_WEBHOOK_SECRET"))


def _webhook_base_url() -> str:
    value = (
        os.getenv("TELEGRAM_WEBHOOK_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("RENDER_API_BASE_URL")
        or DEFAULT_PUBLIC_BASE_URL
    )
    return _clean(value).rstrip("/")


def _webhook_url() -> str:
    return _webhook_base_url() + WEBHOOK_PATH


def _command_body(api_module: Any, text: str) -> str:
    strip_func = getattr(api_module, "_strip_bot", None)
    if callable(strip_func):
        return _clean(strip_func(text))
    value = _clean(text)
    for prefix in ("봇 ", "봇:", "봇아 "):
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def _is_refresh_command(api_module: Any, text: str) -> bool:
    body = _command_body(api_module, text).replace(" ", "").lower()
    return body in REFRESH_COMMANDS


def _extract_message(update: dict[str, Any]) -> tuple[str, str, str] | None:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None

    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    chat_id = _clean(chat.get("id"))
    user_id = _clean(sender.get("id") or chat_id)
    text = _clean(message.get("text"))
    if not chat_id or not text:
        return None
    return chat_id, user_id, text


def _register_webhook() -> None:
    if _clean(os.getenv("TELEGRAM_WEBHOOK_AUTO_REGISTER", "1")).lower() in {"0", "false", "off", "no"}:
        print("[telegram-webhook] auto registration disabled")
        return

    token = _bot_token()
    if not token:
        print("[telegram-webhook] registration skipped: TELEGRAM_BOT_TOKEN missing")
        return

    url = _webhook_url()
    payload: dict[str, Any] = {
        "url": url,
        "allowed_updates": ["message", "edited_message"],
        "drop_pending_updates": False,
    }
    secret = _webhook_secret()
    if secret:
        payload["secret_token"] = secret

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json=payload,
            timeout=20,
        )
        data = response.json() if response.content else {}
        if response.status_code != 200 or not data.get("ok"):
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
        print(f"[telegram-webhook] registered: {url}")
    except Exception as exc:
        print(f"[telegram-webhook] registration failed: {type(exc).__name__}: {exc}")


def _run_refresh(chat_id: str) -> None:
    try:
        print(f"[telegram-webhook] refresh started: chat_id={chat_id}")
        result = generate_and_send_latest_report(
            hours=1,
            limit=999,
            briefing_kind="manual",
            collect=True,
            source="telegram_webhook",
            force_send=True,
            target_chat_ids=[chat_id],
        )
        if result.startswith("리포트 생성 결과가 비어"):
            send_telegram_message(_bot_token(), chat_id, result)
        print(f"[telegram-webhook] refresh completed: chat_id={chat_id}")
    except Exception as exc:
        print(f"[telegram-webhook] refresh failed: {type(exc).__name__}: {exc}")
        try:
            send_telegram_message(
                _bot_token(),
                chat_id,
                f"뉴스갱신 실패: {type(exc).__name__}: {exc}",
            )
        except Exception as send_exc:
            print(f"[telegram-webhook] failure reply failed: {type(send_exc).__name__}: {send_exc}")


def apply(api_module: Any) -> Any:
    app = api_module.app

    if getattr(app.state, "telegram_webhook_installed", False):
        return api_module

    @app.post(WEBHOOK_PATH)
    async def telegram_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
        secret = _webhook_secret()
        if secret:
            received = _clean(request.headers.get("X-Telegram-Bot-Api-Secret-Token"))
            if received != secret:
                raise HTTPException(status_code=403, detail="invalid telegram webhook secret")

        try:
            update = await request.json()
        except Exception:
            update = {}

        parsed = _extract_message(update if isinstance(update, dict) else {})
        if not parsed:
            return {"ok": True, "ignored": True}

        chat_id, user_id, text = parsed
        update_id = _clean(update.get("update_id")) if isinstance(update, dict) else ""
        print(f"[telegram-webhook] update received: update_id={update_id} chat_id={chat_id} text={text[:80]!r}")

        token = _bot_token()
        if not token:
            print("[telegram-webhook] reply failed: TELEGRAM_BOT_TOKEN missing")
            return {"ok": False, "error": "telegram_bot_token_missing"}

        if _is_refresh_command(api_module, text):
            send_telegram_message(token, chat_id, "뉴스 갱신을 시작합니다. 완료되면 새 리포트를 이 대화창으로 보냅니다.")
            background_tasks.add_task(_run_refresh, chat_id)
            return {"ok": True, "queued": True}

        if _command_body(api_module, text).lower() in {"/start", "start"}:
            response_text = api_module._help()
        else:
            response_text = api_module.answer(text, user_id)
        send_telegram_message(token, chat_id, response_text)
        return {"ok": True, "replied": True}

    app.add_event_handler("startup", _register_webhook)
    app.state.telegram_webhook_installed = True
    api_module.API_VERSION = "messenger-telegram-webhook-v1"
    print(f"[telegram-webhook] route installed: {WEBHOOK_PATH}")
    return api_module
