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


def test_regular_evening_note_uses_one_market_snapshot_and_removes_routine_noise(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    monkeypatch.setattr(
        formatter,
        "_outlook",
        lambda *args, **kwargs: SimpleNamespace(
            verdict="중립/혼조",
            score=1,
            confidence="높음",
            evidence_line="축1 지수(30%) +0.00 | 축2 레짐(30%) +0 | 축3 흐름(20%) -1 | 축4 뉴스(20%) +2",
            upside_condition="미국 지수·위험선호 동반 개선",
            downside_condition="미국 지수 약세 지속",
        ),
    )
    now = datetime(2026, 10, 8, 20, 33, tzinfo=KST)
    snapshot = _snapshot()
    snapshot["assets"]["^GSPC"]["change_pct"] = -0.22
    snapshot["assets"]["^IXIC"]["change_pct"] = -0.22
    snapshot["assets"]["^RUT"]["change_pct"] = -1.31
    snapshot["assets"]["^SOX"]["change_pct"] = -1.15
    snapshot["assets"]["^GSPC"]["session_date"] = "2026-10-07"

    cluster = FakeCluster(
        "🔔 삼성전자 실적 호조에도 ETF 리밸런싱 여파 📈 #삼성전자 #반도체",
        "삼성전자 실적 발표 뒤 ETF 리밸런싱 이슈가 부각됐다. 출처: 테스트 | 시각: 2026-10-08T11:04:03+00:00",
        "실적",
        ["AI인프라", "반도체"],
        [_symbol("삼성전자", "005930.KS")],
        99,
        "A",
        "https://example.com/samsung",
    )
    original = "\n".join(
        [
            "1) [99/A] 삼성전자 실적 호조에도 ETF 리밸런싱 여파",
            "🧠 지속학습 상태",
            "  • 누적 성과: 303건",
            "🎯 아침 글로벌 매매전략",
            "1) 미국 반도체(SOXX) LONG",
            "검증: 로컬인사이트엔진 · 원문 16건",
        ]
    )

    note = formatter.build_market_note(
        original_report=original,
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=now,
        market_context={
            "kospi_change_pct": 1.10,
            "kosdaq_change_pct": 4.25,
            "sp500_change_pct": 1.96,
            "nasdaq_change_pct": 2.10,
            "usd_krw": 1345.0,
        },
        snapshot=snapshot,
        selected=[cluster],
    )

    assert "10/08 한국 증시 마감 · 미 증시 프리마켓" in note
    assert "글로벌 마감 시황 노트" not in note
    assert "KOSDAQ +4.25% vs S&P500" not in note
    assert "S&P500 +1.96%" not in note
    assert "S&P500 ▼ -0.22%" in note
    assert "🧠 지속학습 상태" not in note
    assert "🎯 아침 글로벌 매매전략" not in note
    assert "출처 A" not in note
    assert "중요도등급 A" in note
    assert "■ AI인프라" not in note
    assert "■ 반도체" not in note
    assert "하락가 핵심 변수" not in note
    assert "#삼성전자" not in note
    assert "뉴스 범위: 최근 1시간" in note
    assert "미 증시 기준세션 2026-10-07" in note


def test_regular_note_does_not_repeat_korean_headline_in_korea_section(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    monkeypatch.setattr(
        formatter,
        "_outlook",
        lambda *args, **kwargs: SimpleNamespace(
            verdict="중립",
            score=0,
            confidence="중간",
            evidence_line="축1 지수(30%) +0.00",
            upside_condition="확인 필요",
            downside_condition="확인 필요",
        ),
    )
    cluster = FakeCluster(
        "삼성전자 실적 발표",
        "삼성전자 실적 발표가 확인됐다.",
        "실적",
        ["반도체"],
        [_symbol("삼성전자", "005930.KS")],
        90,
        "A",
    )
    note = formatter.build_market_note(
        original_report="검증: 테스트",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=datetime(2026, 10, 8, 20, 33, tzinfo=KST),
        market_context={"kospi_change_pct": 0.2, "kosdaq_change_pct": 0.1},
        snapshot=_snapshot(),
        selected=[cluster],
    )
    korea_block = note.split("■ 한국 증시 관련", 1)[1].split("■ 시황 판정", 1)[0]
    assert "삼성전자(005930.KS) · 상단 핵심요인/섹터에 직접 언급" in korea_block
    assert "삼성전자 실적 발표" not in korea_block

def test_regular_empty_note_aligns_verification_and_hides_internal_axis_math(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    monkeypatch.setattr(
        formatter,
        "_outlook",
        lambda *args, **kwargs: SimpleNamespace(
            verdict="중립/혼조",
            score=1,
            confidence="보통",
            evidence_line="축1 지수(30%) +0.75 | 축2 레짐(30%) +0 | 축3 흐름(20%) +0 | 축4 뉴스(20%) +0",
            upside_condition="주요 지수 강세 확산",
            downside_condition="지수 저점 이탈",
        ),
    )

    note = formatter.build_market_note(
        original_report="검증: 로컬인사이트엔진 · 엄격 · 원문 1건 → 신규 1개 선별 · 중복억제 0건",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=datetime(2026, 10, 10, 8, 30, tzinfo=KST),
        market_context={"usd_krw": 1340.8},
        snapshot=_snapshot(),
        selected=[],
    )

    assert "새 중요 뉴스 없음" in note
    assert "■ 한국 증시 관련" not in note
    assert "축1 지수(30%)" not in note
    assert "신규 0개 선별" in note
    assert "신규 1개 선별" not in note


def test_strategy_judgment_keeps_axis_math():
    outlook = SimpleNamespace(
        verdict="선별 강세",
        score=3,
        confidence="높음",
        evidence_line="축1 지수(30%) +1.00 | 축2 레짐(30%) +1",
        upside_condition="상승 확산",
        downside_condition="지수 이탈",
    )

    lines = formatter._judgment_lines(outlook, detailed=True)

    assert any("축1 지수(30%)" in line for line in lines)
    assert any("축2 레짐(30%)" in line for line in lines)

