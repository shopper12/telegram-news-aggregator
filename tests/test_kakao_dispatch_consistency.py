from telegram_news import kakao_notifier, telegram_dispatch


def test_kakao_chunks_preserve_report_exactly():
    report = (
        "┏━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 미 증시 클로징 노트 ┃\n"
        "┗━━━━━━━━━━━━━━━━━━┛\n\n"
        "📊 마감 지수\n"
        "　다우 ▲ +0.53%　나스닥 ▲ +1.00%\n"
        "■ 장세 요약\n"
        + ("　- 동일한 텔레그램 뉴스 본문을 카카오에도 보낸다.\n" * 80)
        + "📝 한 줄 정리\n　원문 끝"
    )

    chunks = kakao_notifier.split_for_kakao(report, chunk_chars=180)

    assert len(chunks) > 1
    assert "".join(chunks) == report.strip()
    assert all(not chunk.startswith("(1/") for chunk in chunks)


def test_send_kakao_memo_adds_no_chunk_number_to_report(monkeypatch):
    report = "첫 줄\n" + ("뉴스 본문\n" * 120) + "마지막 줄"
    sent: list[str] = []

    monkeypatch.setattr(
        kakao_notifier,
        "refresh_kakao_access_token",
        lambda **kwargs: ("access-token", None),
    )
    monkeypatch.setattr(
        kakao_notifier,
        "_send_text_template",
        lambda *, access_token, text, web_url, button_title: sent.append(text),
    )
    monkeypatch.setattr(kakao_notifier.time, "sleep", lambda _seconds: None)

    kakao_notifier.send_kakao_memo(
        rest_api_key="rest-key",
        refresh_token="refresh-token",
        text=report,
        web_url="https://example.com",
    )

    assert len(sent) > 1
    assert "".join(sent) == report.strip()
    assert not any(part.startswith("(") and "/" in part.splitlines()[0] for part in sent)


def test_kakao_dispatch_uses_same_latest_report_cache_as_telegram(monkeypatch):
    cached_report = "┏━━┓\n┃ 캐시 기준 동일 뉴스 ┃\n┗━━┛\n본문"
    delivered: list[str] = []

    monkeypatch.setattr(
        telegram_dispatch,
        "load_latest_report",
        lambda: {"report": cached_report},
    )

    from telegram_news import app

    monkeypatch.setattr(app, "_send_report_to_kakao", lambda text: delivered.append(text))

    assert telegram_dispatch.dispatch_latest_report_to_kakao(
        "생성 중간값은 달라도 캐시를 사용해야 함",
        raise_on_error=True,
    )
    assert delivered == [cached_report]
