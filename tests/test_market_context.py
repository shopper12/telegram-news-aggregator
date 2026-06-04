from telegram_news.market_data import Quote, get_market_context


def test_get_market_context_falls_back_when_some_data_missing(monkeypatch):
    def fake_fetch_index(label, ticker):
        values = {
            "KOSPI": Quote("KOSPI", 2800.0, 0.5, None, "test", "now"),
            "KOSDAQ": Quote("KOSDAQ", 850.0, -0.2, None, "test", "now"),
            "S&P500": Quote("S&P500", None, None, None, "test", "now", "missing"),
            "NASDAQ": Quote("NASDAQ", None, None, None, "test", "now", "missing"),
            "USD/KRW": Quote("USD/KRW", 1350.0, None, None, "test", "now"),
        }
        return values[label]

    monkeypatch.setattr("telegram_news.market_data._fetch_index", fake_fetch_index)
    monkeypatch.setattr("telegram_news.market_data._fetch_kr_top_sectors_by_volume", lambda: ["반도체", "전력기기"])
    monkeypatch.setattr("telegram_news.market_data._fetch_market_cap_leaders", lambda: ["삼성전자"])

    ctx = get_market_context()
    assert ctx is not None
    assert ctx["kospi_change_pct"] == 0.5
    assert ctx["top_sectors_by_volume"] == ["반도체", "전력기기"]
    assert ctx["market_cap_leaders"] == ["삼성전자"]


def test_get_market_context_returns_none_when_all_data_missing(monkeypatch):
    monkeypatch.setattr(
        "telegram_news.market_data._fetch_index",
        lambda label, ticker: Quote(label, None, None, None, "test", "now", "missing"),
    )
    monkeypatch.setattr("telegram_news.market_data._fetch_kr_top_sectors_by_volume", lambda: [])
    monkeypatch.setattr("telegram_news.market_data._fetch_market_cap_leaders", lambda: [])

    assert get_market_context() is None
