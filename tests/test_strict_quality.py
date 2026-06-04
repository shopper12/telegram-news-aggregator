from types import SimpleNamespace

from telegram_news.strict_quality import materiality_score, strict_filter, MATERIALITY_THRESHOLD


class FakeCluster:
    def __init__(self, *, sector="반도체", news_type="이벤트", title="수주 계약", body="삼성전자 공급 계약", symbols=None, channels=None, raw_score=50):
        self.items = []
        self._sectors = [sector] if sector else []
        self._symbols = symbols if symbols is not None else [SimpleNamespace(name="삼성전자", ticker="005930.KS", asset_type="stock_kr")]
        self._channels = channels or ["a", "b", "c"]
        item = SimpleNamespace(
            title=title,
            body=body,
            channels=self._channels,
            gemini_news_type=news_type,
            gemini_impact="높음",
        )
        impact = SimpleNamespace(impact_level="높음")
        self._best = SimpleNamespace(item=item, news_type=news_type, impact=impact, symbols=self._symbols)
        self._raw_score = raw_score
        self.items = [self._best]

    def best(self):
        return self._best

    def score(self):
        return self._raw_score

    def channel_count(self):
        return len(set(self._channels))

    def sectors(self):
        return self._sectors

    def symbols(self):
        return self._symbols


def test_materiality_score_rewards_core_contract_news(monkeypatch):
    monkeypatch.setattr("telegram_news.strict_quality._symbols_have_market_data", lambda cluster: True)
    cluster = FakeCluster(raw_score=45)
    assert materiality_score(cluster) >= MATERIALITY_THRESHOLD


def test_materiality_score_penalizes_theme_language(monkeypatch):
    monkeypatch.setattr("telegram_news.strict_quality._symbols_have_market_data", lambda cluster: False)
    clean = FakeCluster(title="공급 계약", body="삼성전자 공급 계약", raw_score=45)
    theme = FakeCluster(title="관련주 수혜 전망", body="삼성전자 관련주 수혜 전망", raw_score=45)
    assert materiality_score(theme) < materiality_score(clean)


def test_strict_filter_caps_same_sector(monkeypatch):
    monkeypatch.setattr("telegram_news.strict_quality._symbols_have_market_data", lambda cluster: True)
    clusters = [FakeCluster(sector="반도체", title=f"공급 계약 {i}", raw_score=60) for i in range(4)]
    kept = strict_filter(clusters)
    assert len(kept) == 2
