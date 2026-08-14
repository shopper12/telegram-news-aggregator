from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram_news import market_note_formatter as formatter


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 31, 7, 0, tzinfo=KST)


class FakeCluster:
    def __init__(self, title, body, news_type, sectors, symbols, score=80, grade="B", url=""):
        item = SimpleNamespace(title=title, body=body, source_urls=[url] if url else [], sectors=sectors)
        self._best = SimpleNamespace(item=item, news_type=news_type)
        self._sectors = sectors
        self._symbols = symbols
        self.materiality_score_override = score
        self.materiality_grade_override = grade

    def best(self):
        return self._best

    def sectors(self):
        return self._sectors

    def symbols(self):
        return self._symbols


def _symbol(name, ticker):
    return SimpleNamespace(name=name, ticker=ticker)


def _snapshot():
    return {
        "regime": "risk_on",
        "regime_label": "위험선호",
        "flow_proxy": "성장주 +1.20% / 중소형 -0.50% / 신흥국 +0.30% / 하이일드 +0.10%",
        "assets": {
            "^DJI": {"price": 45000, "change_pct": 0.53},
            "^IXIC": {"price": 23000, "change_pct": 1.00},
            "^GSPC": {"price": 6900, "change_pct": 0.70},
            "^RUT": {"price": 2400, "change_pct": -0.50},
            "^SOX": {"price": 6500, "change_pct": 0.07},
            "^TNX": {"price": 4.73, "change_pct": 0.50},
            "^TYX": {"price": 5.27, "change_pct": 0.40},
            "^VIX": {"price": 18.20, "change_pct": -2.00},
            "CL=F": {"price": 82.40, "change_pct": 1.10},
            "QQQ": {"price": 600, "change_pct": 1.10},
            "EEM": {"price": 50, "change_pct": 0.30},
            "HYG": {"price": 80, "change_pct": 0.10},
            "GLD": {"price": 250, "change_pct": -0.20},
            "TLT": {"price": 90, "change_pct": -0.10},
        },
    }


def test_market_note_uses_requested_closing_note_structure(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    clusters = [
        FakeCluster(
            "아마존 실적 호조로 빅테크 강세",
            "AWS 성장과 AI 수요 확대로 아마존이 급등했다. 장기금리 상승은 중소형주에 부담으로 작용했다.",
            "실적",
            ["미국빅테크", "AI인프라"],
            [_symbol("아마존", "AMZN")],
            92,
            "A",
            "https://example.com/amazon",
        ),
        FakeCluster(
            "메모리 가격 전망과 반도체 차별화",
            "SK하이닉스와 마이크론의 메모리 가격 상승 전망은 유지됐지만 일부 낸드 종목은 실적 우려로 조정받았다.",
            "이벤트",
            ["반도체"],
            [_symbol("SK하이닉스", "000660.KS"), _symbol("마이크론", "MU")],
            76,
            "B",
        ),
    ]
    original = "\n".join(
        [
            "1) [92/A] 아마존 실적 호조로 빅테크 강세",
            "2) [76/B] 메모리 가격 전망과 반도체 차별화",
            "🧠 지속학습 상태",
            "  • 전략 원장: 진행 1건 · 이번 평가 0건 · 이번 학습 0건",
            "🎯 아침 글로벌 매매전략",
            "1) 미국 반도체(SOXX) LONG | 점수 +4.20",
            "  • 진입구간: 300.00 ~ 303.00",
            "선별방식: 뉴스 중요도",
            "검증: 로컬인사이트엔진 · 원문 20건",
        ]
    )

    note = formatter.build_market_note(
        original_report=original,
        summaries=[],
        hours=6,
        timezone_name="Asia/Seoul",
        kind="strategy_morning",
        now=NOW,
        market_context={
            "kospi_change_pct": 0.8,
            "kosdaq_change_pct": 0.5,
            "sp500_change_pct": 0.7,
            "nasdaq_change_pct": 1.0,
            "usd_krw": 1380.5,
        },
        snapshot=_snapshot(),
        selected=clusters,
    )

    assert note.startswith("┏━━━━━━━━")
    assert "07/31 미 증시 클로징 노트" in note
    assert "📊 마감 지수" in note
    assert "다우 ▲ +0.53%" in note
    assert "러셀2000 ▼ -0.50%" in note
    assert "미30년물 5.27%" in note
    assert "WTI $82.40" in note
    assert "■ 장세 요약" in note
    assert "■ 변화 요인 ①" in note
    assert "■ 미국빅테크" in note or "■ AI인프라" in note
    assert "■ 반도체" in note
    assert "■ 한국 증시 관련" in note
    assert "SK하이닉스(000660.KS)" in note
    assert "■ 시황 판정" in note
    assert "🎯 아침 글로벌 매매전략" in note
    assert "📝 한 줄 정리" in note
    assert note.index("📊 마감 지수") < note.index("■ 장세 요약")
    assert note.index("■ 장세 요약") < note.index("■ 변화 요인 ①")
    assert note.index("■ 한국 증시 관련") < note.index("📝 한 줄 정리")


def test_market_note_does_not_invent_unobserved_options_story(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    cluster = FakeCluster(
        "수출 계약 확대",
        "공급 계약 체결과 매출 증가가 확인됐다.",
        "이벤트",
        ["전력기기"],
        [_symbol("LS ELECTRIC", "010120.KS")],
        72,
        "B",
    )

    note = formatter.build_market_note(
        original_report="검증: 테스트",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="kr_premarket",
        now=NOW,
        market_context={
            "kospi_change_pct": 0.2,
            "kosdaq_change_pct": -0.1,
            "sp500_change_pct": 0.1,
            "nasdaq_change_pct": 0.2,
        },
        snapshot=_snapshot(),
        selected=[cluster],
    )

    assert "감마 스퀴즈" not in note
    assert "0DTE" not in note
    assert "CTA" not in note
    assert "미확인 옵션/수급 서사 생성 금지" in note


def test_messenger_bridge_prefers_cached_formatted_note(monkeypatch):
    cached = "┏━━━━━━━━━━┓\n┃ 저장된 시황 노트 ┃\n┗━━━━━━━━━━┛"
    api = SimpleNamespace(
        _news=lambda: "실시간 헤드라인",
        _market_note_bridge_installed=False,
    )

    monkeypatch.setattr(
        "telegram_news.report_cache.load_latest_report",
        lambda: {"report": cached},
    )

    formatter.install_messenger_bridge(api)

    assert api._news() == cached


def test_messenger_bridge_expands_reply_route_limit(monkeypatch):
    class Route:
        def __init__(self):
            self.path = "/reply"
            self.methods = {"GET"}
            self.endpoint = None
            self.dependant = SimpleNamespace(call=None)

    route = Route()
    long_note = "가" * 6000
    api = SimpleNamespace(
        _news=lambda: "기존",
        _market_note_bridge_installed=False,
        app=SimpleNamespace(routes=[route]),
        _query_message=lambda request: "봇 뉴스",
        _query_user=lambda request: "tester",
        answer=lambda message, user_id: long_note,
    )

    monkeypatch.setattr(
        "telegram_news.report_cache.load_latest_report",
        lambda: {"report": long_note},
    )

    formatter.install_messenger_bridge(api)
    response = route.dependant.call(SimpleNamespace(query_params={}))

    assert len(response) == 6000
    assert response == long_note
