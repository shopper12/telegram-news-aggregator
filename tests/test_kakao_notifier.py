from telegram_news import kakao_notifier


def test_split_for_kakao_keeps_short_text():
    assert kakao_notifier.split_for_kakao("hello", chunk_chars=10) == ["hello"]


def test_split_for_kakao_splits_long_text_without_ellipsis():
    text = "가" * 25
    chunks = kakao_notifier.split_for_kakao(text, chunk_chars=10)
    assert chunks == ["가" * 10, "가" * 10, "가" * 5]
    assert "".join(chunks) == text


def test_send_kakao_memo_posts_all_chunks(monkeypatch):
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
    monkeypatch.setattr(kakao_notifier.time, "sleep", lambda _: None)
    monkeypatch.setenv("KAKAO_TEXT_CHUNK_CHARS", "180")
    rotated = kakao_notifier.send_kakao_memo(
        rest_api_key="rest",
        refresh_token="refresh",
        text="가" * 220,
        client_secret=None,
    )

    assert rotated is None
    assert calls[0][0] == kakao_notifier.KAKAO_TOKEN_URL
    assert calls[1][0] == kakao_notifier.KAKAO_MEMO_URL
    assert calls[2][0] == kakao_notifier.KAKAO_MEMO_URL
    assert calls[1][1]["headers"]["Authorization"] == "Bearer access"
