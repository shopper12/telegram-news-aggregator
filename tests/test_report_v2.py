from types import SimpleNamespace

from telegram_news import strict_report_v2 as report_v2


class FakeCluster:
    def __init__(self, news_type="이벤트", title="삼성전자 공급 계약", body="삼성전자 공급 계약 공시", sector="반도체"):
        self._sectors = [sector]
        self._symbols = [SimpleNamespace(name="삼성전자", ticker="005930.KS")]
        item = SimpleNamespace(title=title, body=body, risk="선반영 가능성 확인 필요", channels=["a"])
        impact = SimpleNamespace(impact_level="높음")
        self._best = SimpleNamespace(item=item, news_type=news_type, impact=impact, symbols=self._symbols, reasons=["이벤트", "종목직접"])
        self.items = [self._best]

    def best(self):
        return self._best

    def sectors(self):
        return self._sectors

    def symbols(self):
        return self._symbols

    def channel_count(self):
        return 1

    def score(self):
        return 80


def test_audit_rejects_crypto_and_hard_trading_language():
    ok, reason = report_v2._audit_report_text("비트코인 뉴스\n진입고려: [관망]", [FakeCluster()])
    assert not ok
    assert reason == "crypto_leak"

    ok, reason = report_v2._audit_report_text("목표가 제시\n진입고려: [관망]", [FakeCluster()])
    assert not ok
    assert reason == "hard_trading_instruction"


def test_entry_consideration_for_price_reaction_is_watch():
    cluster = FakeCluster(news_type="가격반응", title="삼성전자 급등", body="삼성전자 신고가")
    assert report_v2._entry_consideration(cluster).startswith("[관망]")


def test_local_report_uses_market_context(monkeypatch):
    monkeypatch.setattr("telegram_news.strict_report_v2.materiality_score", lambda cluster: 88)
    monkeypatch.setattr("telegram_news.strict_report_v2.materiality_grade", lambda cluster: "B+")
    text = report_v2._local_insight_report(
        now=__import__("datetime").datetime(2026, 6, 1, 8, 30),
        kind="kr_premarket",
        hours=12,
        selected=[FakeCluster()],
        stock_count=1,
        blocked=0,
        rule="엄격",
        overview="KOSPI 2800 +0.50%",
        source_count=1,
        pre_gate_count=1,
        market_context={"kospi_change_pct": 0.5, "kosdaq_change_pct": -0.2, "top_sectors_by_volume": ["반도체"]},
        engine="테스트엔진",
    )
    assert "장전 뉴스 브리핑" in text
    assert "KOSPI +0.50%" in text
    assert "진입고려" in text
