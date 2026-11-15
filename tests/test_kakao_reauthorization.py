from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from scripts import renew_kakao_token as renewal


def test_authorize_url_requests_message_scope_and_state():
    url = renewal.build_authorize_url("restkey", "http://localhost:4000/redirect", "abc123")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "kauth.kakao.com"
    assert query["client_id"] == ["restkey"]
    assert query["scope"] == ["talk_message"]
    assert query["state"] == ["abc123"]


def test_callback_requires_exact_redirect_and_state():
    uri = "http://localhost:4000/redirect"
    assert renewal.extract_authorization_code(
        uri + "?code=token-code&state=expected", uri, "expected"
    ) == "token-code"
    with pytest.raises(ValueError, match="state mismatch"):
        renewal.extract_authorization_code(uri + "?code=x&state=wrong", uri, "expected")
    with pytest.raises(ValueError, match="Redirect URI"):
        renewal.extract_authorization_code(
            "https://another.example/?code=x&state=expected", uri, "expected"
        )


def test_token_exchange_uses_authorization_code_and_secret(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"refresh_token": "sensitive-token", "access_token": "short-lived"}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(renewal.requests, "post", fake_post)
    token = renewal.exchange_refresh_token(
        "client", "http://localhost:4000/redirect", "one-time-code", "secret"
    )
    assert token == "sensitive-token"
    body = calls[0][1]["data"]
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "one-time-code"
    assert body["client_secret"] == "secret"


def test_gh_secret_set_passes_token_only_through_stdin(monkeypatch):
    kwargs_list = []
    monkeypatch.setattr(renewal.shutil, "which", lambda _: "/usr/bin/gh")

    def fake_run(args, **kwargs):
        kwargs_list.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(renewal.subprocess, "run", fake_run)
    assert renewal.save_to_github_secret("private-refresh-token") is True
    args, kwargs = kwargs_list[0]
    assert "private-refresh-token" not in args
    assert kwargs["input"] == "private-refresh-token"
    assert args[:3] == ["/usr/bin/gh", "secret", "set"]
