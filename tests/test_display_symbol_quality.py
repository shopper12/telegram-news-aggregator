from types import SimpleNamespace

from telegram_news import strict_report_v2


class FakeCluster:
    def __init__(self, title, body, symbols):
        item = SimpleNamespace(title=title, body=body)
        self._best = SimpleNamespace(item=item)
        self._symbols = symbols

    def best(self):
        return self._best

    def symbols(self):
        return self._symbols


def _symbol(name, ticker):
    return SimpleNamespace(name=name, ticker=ticker)


def test_display_symbols_rejects_source_domain_and_generic_etf_false_positives():
    cluster = FakeCluster(
        "삼성전자 실적 호조에도 ETF 리밸런싱 여파",
        "삼성전자 실적 발표 기사. 원문 https://m.stock.naver.com/investment/news/mainnews/009/0000000000",
        [
            _symbol("삼성전자", "005930.KS"),
            _symbol("NAVER", "035420.KS"),
            _symbol("ETF", "ZZZTT"),
        ],
    )

    symbols = strict_report_v2._display_symbols(cluster)

    assert [(symbol.name, symbol.ticker) for symbol in symbols] == [("삼성전자", "005930.KS")]


def test_display_symbols_keeps_explicit_ticker_even_for_generic_named_security():
    cluster = FakeCluster(
        "ETF (SPY) 거래 동향",
        "미국 ETF (SPY)가 직접 언급됐다.",
        [_symbol("ETF", "SPY")],
    )

    symbols = strict_report_v2._display_symbols(cluster)

    assert [symbol.ticker for symbol in symbols] == ["SPY"]
