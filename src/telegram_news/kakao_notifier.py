from __future__ import annotations

import json
import os
import time

import requests

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
DEFAULT_KAKAO_TEXT_CHARS = 900


def refresh_kakao_access_token(rest_api_key: str, refresh_token: str, client_secret: str | None = None) -> tuple[str, str | None]:
    """Return a short-lived Kakao access token and an optional rotated refresh token.

    Kakao only returns a new refresh_token when the existing one is close to expiry.
    The caller should update the GitHub secret manually if a rotated token is printed.
    """
    data = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret

    response = requests.post(
        KAKAO_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Kakao token refresh failed: access_token missing")
    return str(access_token), payload.get("refresh_token")


def _chunk_size() -> int:
    raw = os.getenv("KAKAO_TEXT_CHUNK_CHARS")
    if not raw:
        return DEFAULT_KAKAO_TEXT_CHARS
    try:
        return max(180, min(950, int(raw)))
    except ValueError:
        return DEFAULT_KAKAO_TEXT_CHARS


def split_for_kakao(text: str, chunk_chars: int | None = None) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    limit = chunk_chars or _chunk_size()
    chunks: list[str] = []
    current = ""

    for block in text.split("\n"):
        candidate = block if not current else current + "\n" + block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        current = block

    if current:
        chunks.append(current)
    return chunks


def _send_text_template(access_token: str, text: str, web_url: str, button_title: str) -> None:
    template_object = {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": web_url,
            "mobile_web_url": web_url,
        },
        "button_title": button_title,
    }
    response = requests.post(
        KAKAO_MEMO_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        },
        data={"template_object": json.dumps(template_object, ensure_ascii=False)},
        timeout=20,
    )
    response.raise_for_status()


def send_kakao_memo(
    *,
    rest_api_key: str,
    refresh_token: str,
    text: str,
    client_secret: str | None = None,
    web_url: str = "https://github.com/shopper12/telegram-news-aggregator",
) -> str | None:
    access_token, rotated_refresh_token = refresh_kakao_access_token(
        rest_api_key=rest_api_key,
        refresh_token=refresh_token,
        client_secret=client_secret,
    )
    chunks = split_for_kakao(text)
    total = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        prefix = f"({idx}/{total})\n" if total > 1 else ""
        _send_text_template(
            access_token=access_token,
            text=prefix + chunk,
            web_url=web_url,
            button_title="리포트 확인" if total == 1 else f"리포트 {idx}/{total}",
        )
        if idx < total:
            time.sleep(0.35)
    return rotated_refresh_token
