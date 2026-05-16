from telegram_news.normalizer import normalize_text, deduplicate_rows


def test_normalize_text_removes_url():
    text = "속보 원전 수주 https://example.com"
    assert normalize_text(text) == "속보 원전 수주"


def test_deduplicate_rows():
    rows = [
        {
            "normalized_text": "원전 수주 기대감 확대",
            "text": "원전 수주 기대감 확대",
            "channel_name": "A",
            "category": "stock",
            "message_date": "2026-05-16T01:00:00+00:00",
        },
        {
            "normalized_text": "원전 수주 기대감 확대!",
            "text": "원전 수주 기대감 확대!",
            "channel_name": "B",
            "category": "stock",
            "message_date": "2026-05-16T01:01:00+00:00",
        },
    ]
    result = deduplicate_rows(rows)
    assert len(result) == 1
    assert result[0].count == 2
