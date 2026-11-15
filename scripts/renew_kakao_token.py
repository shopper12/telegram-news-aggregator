"""Interactive, local-only recovery of a Kakao memo OAuth refresh token.

Never run this in GitHub Actions; auth codes and tokens must not enter job logs.
Requires the exact redirect URI registered for your Kakao REST API key.
"""
from __future__ import annotations

import getpass
import os
import secrets
import shutil
import subprocess
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv


REPOSITORY = "shopper12/telegram-news-aggregator"
DEFAULT_REDIRECT_URI = "http://localhost:4000/redirect"


def build_authorize_url(rest_api_key: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "talk_message",
        "state": state,
    }
    return "https://kauth.kakao.com/oauth/authorize?" + urlencode(params)


def extract_authorization_code(redirected_url: str, redirect_uri: str, state: str) -> str:
    returned = urlparse(redirected_url.strip())
    expected = urlparse(redirect_uri)
    if (returned.scheme, returned.netloc, returned.path) != (
        expected.scheme, expected.netloc, expected.path
    ):
        raise ValueError("Redirect URI does not match the registered URI.")
    query = parse_qs(returned.query)
    if query.get("state", [""])[0] != state:
        raise ValueError("OAuth state mismatch; restart authorization.")
    if query.get("error"):
        raise ValueError("Kakao authorization failed; check app login and talk_message consent.")
    code = query.get("code", [""])[0]
    if not code:
        raise ValueError("Missing OAuth code in callback URL.")
    return code


def exchange_refresh_token(
    rest_api_key: str, redirect_uri: str, code: str, client_secret: str = ""
) -> str:
    fields = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if client_secret:
        fields["client_secret"] = client_secret
    response = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=fields,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        timeout=25,
    )
    if response.status_code != 200:
        # Deliberately do not echo URLs, codes, app secrets, or response bodies.
        raise RuntimeError(
            f"Kakao token exchange failed (HTTP {response.status_code}). "
            "Check the registered redirect URI and REST API client secret."
        )
    token = response.json().get("refresh_token")
    if not token or not isinstance(token, str):
        raise RuntimeError("Kakao response did not contain a refresh token.")
    return token


def save_to_github_secret(refresh_token: str) -> bool:
    executable = shutil.which("gh")
    if not executable:
        return False
    try:
        # stdin avoids exposing the token in process arguments or shell history.
        result = subprocess.run(
            [executable, "secret", "set", "KAKAO_REFRESH_TOKEN", "--repo", REPOSITORY],
            input=refresh_token,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> None:
    load_dotenv()
    print("Kakao reauthorization: local PC only. Do not paste credentials into chat.")
    print("Confirm Kakao Login is ON, talk_message consent is enabled, and")
    print("the following URI is registered in Kakao Developers: " + DEFAULT_REDIRECT_URI)
    rest_api_key = os.getenv("KAKAO_REST_API_KEY") or getpass.getpass("Kakao REST API key: ").strip()
    redirect_uri = (
        os.getenv("KAKAO_REDIRECT_URI")
        or input(f"Registered redirect URI [{DEFAULT_REDIRECT_URI}]: ").strip()
        or DEFAULT_REDIRECT_URI
    )
    client_secret = os.getenv("KAKAO_CLIENT_SECRET") or getpass.getpass(
        "Kakao client secret (Enter if disabled): "
    ).strip()
    if not rest_api_key:
        raise SystemExit("Kakao REST API key is required.")

    state = secrets.token_urlsafe(24)
    url = build_authorize_url(rest_api_key, redirect_uri, state)
    print("\nLog in to Kakao and approve the talk_message permission:")
    print(url)
    webbrowser.open(url)
    print("\nAfter consent, copy the entire redirected URL from the browser.")
    print("A localhost connection error is normal if no local server is running.")
    redirected_url = getpass.getpass("Paste callback URL (hidden): ").strip()
    code = extract_authorization_code(redirected_url, redirect_uri, state)
    token = exchange_refresh_token(rest_api_key, redirect_uri, code, client_secret)
    print("Kakao reauthorization succeeded.")
    if save_to_github_secret(token):
        print("GitHub Actions secret KAKAO_REFRESH_TOKEN updated securely via gh CLI.")
        return
    print("GitHub CLI is not installed or not authenticated.")
    print(f"Open https://github.com/{REPOSITORY}/settings/secrets/actions")
    print("Create/update KAKAO_REFRESH_TOKEN with the new value.")
    if input("Show the token ON THIS PRIVATE PC for manual copy? [y/N]: ").strip().lower() == "y":
        print("New KAKAO_REFRESH_TOKEN (do not share or commit):")
        print(token)
    else:
        print("Token was not displayed or persisted; rerun if manual update is needed.")


if __name__ == "__main__":
    main()
