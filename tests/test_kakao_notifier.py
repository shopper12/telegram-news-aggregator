from telegram_news import kakao_notifier


def test_trim_for_kakao_keeps_short_text():
    assert kakao_notifier._trim_for_kakao("hello") == "hello"


def test_trim_for_kakao_truncates_long_text():
    text = "가" * 1000
    trimmed = kakao_notifier._trim_for_kakao(text)
    assert len(trimmed) <= kakao_notifier.MAX_KAKAO_TEXT_CHARS
    assert "이하 생략" in trimmed


def test_send_kakao_memo_posts_template(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, payload=None):
            self._payload = payload or {}
        def raise_for_status(self):
            return None
        def json(self):
            return self._payload

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url == kakao_notifier.KAKAO_TOKEN_URL:
            return Resp({"access_token": "access"})
        return Resp({})

    monkeypatch.setattr(kakao_notifier.requests, "post", fake_post)
    rotated = kakao_notifier.send_kakao_memo(
        rest_api_key="rest",
        refresh_token="refresh",
        text="report",
        client_secret=None,
    )

    assert rotated is None
    assert calls[0][0] == kakao_notifier.KAKAO_TOKEN_URL
    assert calls[1][0] == kakao_notifier.KAKAO_MEMO_URL
    assert calls[1][1]["headers"]["Authorization"] == "Bearer access"
