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


def test_kospi_5290_is_not_rejected_by_outdated_upper_limit():
    from telegram_news import market_data

    quote = Quote("KOSPI", 5290.53, -0.98, None, "verified", "2026-11-13")
    assert market_data._valid_quote("KOSPI", quote).price == 5290.53


def test_yahoo_uses_previous_session_close_not_chart_range_close(monkeypatch):
    from telegram_news import market_data

    payload = {
        "chart": {"result": [{
            "meta": {
                "regularMarketPrice": 699.66,
                "chartPreviousClose": 644.30,
                "previousClose": 696.52,
            }
        }]}
    }
    monkeypatch.setattr(market_data, "_json_get", lambda url: payload)
    quote = market_data._fetch_us_quote("^KQ11")
    assert quote.price == 699.66
    assert round(quote.change_pct, 2) == 0.45
    assert round(quote.change_pct, 2) != 8.59


def test_large_kosdaq_move_requires_two_sources():
    from telegram_news import market_data

    first = Quote("KOSDAQ", 699.66, 8.60, None, "Yahoo Finance", "now")
    price_only = Quote("KOSDAQ", 699.66, None, None, "Naver Finance HTML", "now")
    blocked = market_data._first_valid_index("KOSDAQ", first, price_only)
    assert blocked.price == 699.66
    assert blocked.change_pct is None
    assert "independent confirmation" in blocked.error


def test_large_kosdaq_move_with_independent_confirmation_is_retained():
    from telegram_news import market_data

    first = Quote("KOSDAQ", 700.0, 8.60, None, "Naver", "now")
    second = Quote("KOSDAQ", 700.1, 8.55, None, "Yahoo", "now")
    assert market_data._first_valid_index("KOSDAQ", first, second).change_pct == 8.60
