from __future__ import annotations

import json
import os
import time

import requests

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
DEFAULT_KAKAO_TEXT_CHARS = 900


def _response_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = str(payload.get("error") or "").strip()
            description = str(payload.get("error_description") or payload.get("msg") or "").strip()
            code = payload.get("code")
            parts = [part for part in [error, description, f"code={code}" if code is not None else ""] if part]
            if parts:
                return " | ".join(parts)
    except Exception:
        pass
    return str(response.text or "").strip()[:500] or "no response body"


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
    if response.status_code >= 400:
        raise RuntimeError(
            f"Kakao token refresh failed: HTTP {response.status_code}: {_response_detail(response)}"
        )
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
    """Split Kakao messages without adding, deleting, or reordering report text.

    Kakao's text template is much shorter than Telegram's message limit, so a long
    report must be delivered as multiple Kakao messages. The chunks are exact
    substrings of the normalized report: ``''.join(chunks)`` is always identical
    to ``text.strip()``. Newline boundaries are preferred when possible, but the
    newline itself is kept in one chunk so no report content is lost.
    """
    normalized = (text or "").strip()
    if not normalized:
        return []

    limit = chunk_chars or _chunk_size()
    chunks: list[str] = []
    remaining = normalized
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < max(1, limit // 2):
            split_at = limit
        else:
            split_at += 1  # preserve the newline in the emitted chunk
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
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
    if response.status_code >= 400:
        raise RuntimeError(
            f"Kakao memo send failed: HTTP {response.status_code}: {_response_detail(response)}"
        )


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
        _send_text_template(
            access_token=access_token,
            text=chunk,
            web_url=web_url,
            button_title="리포트 확인" if total == 1 else f"리포트 {idx}/{total}",
        )
        if idx < total:
            time.sleep(0.35)
    return rotated_refresh_token
