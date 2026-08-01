from telegram_news import messenger_api


def test_parse_urlencoded_payload_decodes_messengerbotr_fields():
    raw = (
        "message=%EB%B4%87+%EB%89%B4%EC%8A%A4"
        "&sender=%EC%83%81%EB%8B%B4%EB%B0%A9%3A%3Aj"
    )

    payload = messenger_api._parse_urlencoded_payload(raw)

    assert payload["message"] == "봇 뉴스"
    assert payload["sender"] == "상담방::j"


def test_decoded_messengerbotr_news_command_reaches_news_handler(monkeypatch):
    payload = messenger_api._parse_urlencoded_payload(
        "message=%EB%B4%87+%EB%89%B4%EC%8A%A4&sender=test-user"
    )
    monkeypatch.setattr(messenger_api, "_news", lambda: "뉴스 응답 정상")

    result = messenger_api.answer(payload["message"], payload["sender"])

    assert result == "뉴스 응답 정상"


def test_non_form_raw_text_is_not_misparsed():
    assert messenger_api._parse_urlencoded_payload("봇 뉴스") == {}
