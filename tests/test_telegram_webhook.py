from types import SimpleNamespace

from fastapi import FastAPI

from telegram_news import telegram_webhook as webhook


VALID_TEST_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


class FakeApi:
    @staticmethod
    def _strip_bot(text: str) -> str:
        value = str(text or "").strip()
        return value[2:].strip() if value.startswith("봇 ") else value


def test_extract_message_from_telegram_update():
    update = {
        "update_id": 123,
        "message": {
            "from": {"id": 77},
            "chat": {"id": -10055},
            "text": "봇 뉴스갱신",
        },
    }

    assert webhook._extract_message(update) == ("-10055", "77", "봇 뉴스갱신")


def test_refresh_command_accepts_bot_prefix():
    assert webhook._is_refresh_command(FakeApi, "봇 뉴스갱신") is True
    assert webhook._is_refresh_command(FakeApi, "봇 refresh") is True
    assert webhook._is_refresh_command(FakeApi, "봇 뉴스") is False


def test_token_diagnostics_never_exposes_raw_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TEST_TOKEN)

    diagnostics = webhook._token_diagnostics()

    assert diagnostics["configured"] is True
    assert diagnostics["format_valid"] is True
    assert diagnostics["token_length"] == len(VALID_TEST_TOKEN)
    assert len(diagnostics["token_fingerprint"]) == 12
    assert VALID_TEST_TOKEN not in repr(diagnostics)


def test_register_webhook_verifies_token_before_registration(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TEST_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_AUTO_REGISTER", "1")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_BASE_URL", "https://example.onrender.com/")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    calls = []

    class FakeResponse:
        status_code = 200
        content = b"{}"
        text = '{"ok":true}'

        def __init__(self, result):
            self.result = result

        def json(self):
            return {"ok": True, "result": self.result}

    def fake_post(url, json, timeout):
        calls.append(("post", url, json, timeout))
        return FakeResponse(True)

    def fake_get(url, timeout):
        calls.append(("get", url, None, timeout))
        if url.endswith("/getMe"):
            return FakeResponse({"id": 123456789, "username": "example_bot"})
        return FakeResponse({
            "url": "https://example.onrender.com/telegram/webhook",
            "pending_update_count": 0,
        })

    monkeypatch.setattr(webhook.requests, "post", fake_post)
    monkeypatch.setattr(webhook.requests, "get", fake_get)
    webhook._register_webhook()

    assert calls[0][0] == "get"
    assert calls[0][1].endswith("/getMe")
    assert calls[1][0] == "post"
    assert calls[1][1].endswith("/setWebhook")
    assert calls[1][2]["url"] == "https://example.onrender.com/telegram/webhook"
    assert calls[1][2]["allowed_updates"] == ["message", "edited_message"]
    assert calls[2][1].endswith("/getWebhookInfo")
    output = capsys.readouterr().out
    assert "token verified" in output
    assert VALID_TEST_TOKEN not in output


def test_safe_webhook_info_identifies_unauthorized_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TEST_TOKEN)

    def reject(_method, *, payload=None):
        raise RuntimeError('Telegram getMe failed: HTTP 401: {"ok":false,"description":"Unauthorized"}')

    monkeypatch.setattr(webhook, "_telegram_api", reject)
    result = webhook._safe_webhook_info()

    assert result["ok"] is False
    assert result["token_status"] == "unauthorized_or_revoked"
    assert result["token"]["configured"] is True
    assert result["token"]["format_valid"] is True
    assert "fresh API token" in result["required_action"]
    assert VALID_TEST_TOKEN not in repr(result)


def test_apply_installs_routes_and_startup_handler_on_fastapi():
    app = FastAPI()
    fake_api = SimpleNamespace(app=app, API_VERSION="old")

    result = webhook.apply(fake_api)

    route_paths = {route.path for route in app.routes}
    assert result is fake_api
    assert "/telegram/webhook/status" in route_paths
    assert "/telegram/webhook" in route_paths
    assert webhook._register_webhook in app.router.on_startup
    assert fake_api.API_VERSION == "messenger-telegram-webhook-v3"
