from types import SimpleNamespace

from telegram_news import telegram_webhook as webhook


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


def test_register_webhook_uses_render_public_url(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
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
        return FakeResponse({
            "url": "https://example.onrender.com/telegram/webhook",
            "pending_update_count": 0,
        })

    monkeypatch.setattr(webhook.requests, "post", fake_post)
    monkeypatch.setattr(webhook.requests, "get", fake_get)
    webhook._register_webhook()

    assert calls[0][1].endswith("/setWebhook")
    assert calls[0][2]["url"] == "https://example.onrender.com/telegram/webhook"
    assert calls[0][2]["allowed_updates"] == ["message", "edited_message"]
    assert calls[1][1].endswith("/getWebhookInfo")
    assert "registered" in capsys.readouterr().out


def test_apply_installs_routes_and_startup_handler():
    routes = []
    handlers = []

    class FakeApp:
        state = SimpleNamespace()

        def get(self, path):
            def decorator(func):
                routes.append(("get", path, func))
                return func
            return decorator

        def post(self, path):
            def decorator(func):
                routes.append(("post", path, func))
                return func
            return decorator

        def add_event_handler(self, event, handler):
            handlers.append((event, handler))

    fake_api = SimpleNamespace(app=FakeApp(), API_VERSION="old")
    result = webhook.apply(fake_api)

    assert result is fake_api
    assert ("get", "/telegram/webhook/status") in [(method, path) for method, path, _ in routes]
    assert ("post", "/telegram/webhook") in [(method, path) for method, path, _ in routes]
    assert handlers[0][0] == "startup"
    assert fake_api.API_VERSION == "messenger-telegram-webhook-v2"
