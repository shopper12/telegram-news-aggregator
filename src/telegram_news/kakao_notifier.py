from __future__ import annotations

import json

import requests

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
MAX_KAKAO_TEXT_CHARS = 900


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


def _trim_for_kakao(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_KAKAO_TEXT_CHARS:
        return text
    return text[: MAX_KAKAO_TEXT_CHARS - 20].rstrip() + "\n… 이하 생략"


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
    template_object = {
        "object_type": "text",
        "text": _trim_for_kakao(text),
        "link": {
            "web_url": web_url,
            "mobile_web_url": web_url,
        },
        "button_title": "리포트 확인",
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
    return rotated_refresh_token
