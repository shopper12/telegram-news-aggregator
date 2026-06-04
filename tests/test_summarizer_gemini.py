from telegram_news.normalizer import DedupedItem
from telegram_news.summarizer import local_summarize, gemini_classify_if_available


def _item(text, categories=None, count=1):
    return DedupedItem(
        text=text,
        channel_names=["news"],
        categories=categories or ["korea_stock"],
        count=count,
        message_dates=["2026-01-01"],
        message_urls=[],
    )


def test_local_summarize_uses_market_type_from_categories():
    summaries = local_summarize([_item("엔비디아 NVDA GPU 데이터센터", ["us_stock"])])
    assert summaries[0].sectors
    assert "미국빅테크" in summaries[0].sectors


def test_gemini_classify_falls_back_without_key():
    summaries = gemini_classify_if_available([_item("삼성전자 공급 승인")], api_key=None, model="gemini-flash-latest")
    assert len(summaries) == 1
    assert summaries[0].gemini_news_type == ""


def test_gemini_classify_graceful_fallback_on_error(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network blocked")

    monkeypatch.setattr("telegram_news.summarizer._gemini_classify_batch", boom)
    summaries = gemini_classify_if_available([_item("삼성전자 공급 승인")], api_key="dummy", model="gemini-flash-latest")
    assert len(summaries) == 1
    assert summaries[0].gemini_news_type == ""
