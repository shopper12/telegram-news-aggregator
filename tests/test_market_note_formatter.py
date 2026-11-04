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
    assert "ETF 수익률은 자금유입/유출이 아니라 가격 프록시" in note


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


def _with_session_dates(snapshot, date="2026-10-07"):
    for item in snapshot["assets"].values():
        item["session_date"] = date
    return snapshot


def test_regular_evening_note_is_decision_useful_and_does_not_claim_premarket_or_fund_flows(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    monkeypatch.setattr(
        formatter,
        "_score_grade",
        lambda cluster: (cluster.materiality_score_override, cluster.materiality_grade_override),
    )
    now = datetime(2026, 10, 8, 20, 33, tzinfo=KST)
    snapshot = _with_session_dates(_snapshot())
    snapshot["assets"]["^GSPC"]["change_pct"] = -0.22
    snapshot["assets"]["^IXIC"]["change_pct"] = -0.22
    snapshot["assets"]["^RUT"]["change_pct"] = -1.31
    snapshot["assets"]["^SOX"]["change_pct"] = -1.15

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
            "검증: 로컬인사이트엔진 · 엄격 · 원문 16건 → 신규 1개 선별 · 중복억제 0건",
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

    assert "10/08 한국 증시 마감 · 미국 최근 확정세션" in note
    assert "프리마켓" not in note
    assert "KOSPI ▲ +1.10%" in note
    assert "KOSDAQ ▲ +4.25%" in note
    assert "S&P500 ▼ -0.22%" in note
    assert "핵심 변화" in note
    assert "다음 확인" in note
    assert "삼성전자 실적 호조에도 ETF 리밸런싱 여파" in note
    assert "한국 상장사 직접 재료: 삼성전자(005930.KS)" in note
    assert "복수 원문 확인" in note
    assert "ETF 가격 프록시 2026-10-07:" in note
    assert "성장주" not in note
    assert "자금 흐름" not in note
    assert "4축 판정" not in note
    assert "■ 시황 판정" not in note
    assert "중요도등급" not in note
    assert "🧠 지속학습 상태" not in note
    assert "🎯 아침 글로벌 매매전략" not in note
    assert "#삼성전자" not in note
    assert "미국 확정세션 2026-10-07" in note


def test_regular_note_drops_single_source_d_even_when_raw_score_is_97(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    monkeypatch.setattr(
        formatter,
        "_score_grade",
        lambda cluster: (cluster.materiality_score_override, cluster.materiality_grade_override),
    )
    cluster = FakeCluster(
        "SharkNinja 2026년 3분기 실적 요약",
        "매출과 EBITDA가 증가했다.",
        "실적",
        ["소비재"],
        [],
        97,
        "D",
        "",
    )
    note = formatter.build_market_note(
        original_report="1) [97/D] SharkNinja 2026년 3분기 실적 요약\n검증: 로컬인사이트엔진 · 엄격 · 원문 38건 → 신규 1개 선별 · 중복억제 0건",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=datetime(2026, 11, 4, 20, 48, tzinfo=KST),
        market_context={"kospi_change_pct": -0.2, "kosdaq_change_pct": 0.1, "usd_krw": 1338.6},
        snapshot=_with_session_dates(_snapshot(), "2026-11-03"),
        selected=[cluster],
    )

    assert "SharkNinja" not in note
    assert "새로 확인된 중요 변화 없음" in note
    assert "단일출처 D급" in note
    assert "신규 0개 선별" in note


def test_regular_note_keeps_verified_korean_direct_event_without_sector_dump(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    monkeypatch.setattr(
        formatter,
        "_score_grade",
        lambda cluster: (cluster.materiality_score_override, cluster.materiality_grade_override),
    )
    cluster = FakeCluster(
        "삼성전자 HBM 공급계약 확정",
        "삼성전자가 HBM 공급계약 체결을 공시했다.",
        "공시/확정",
        ["반도체", "AI인프라"],
        [_symbol("삼성전자", "005930.KS")],
        91,
        "B",
        "https://example.com/disclosure",
    )
    note = formatter.build_market_note(
        original_report="1) [91/B] 삼성전자 HBM 공급계약 확정\n검증: 테스트",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=datetime(2026, 11, 4, 20, 48, tzinfo=KST),
        market_context={"kospi_change_pct": 0.2, "kosdaq_change_pct": 0.1},
        snapshot=_with_session_dates(_snapshot(), "2026-11-03"),
        selected=[cluster],
    )

    assert "삼성전자 HBM 공급계약 확정" in note
    assert "한국 상장사 직접 재료" in note
    assert "원문 확인" in note
    assert "■ AI인프라" not in note
    assert "■ 반도체" not in note
    assert "파생 관찰 섹터" not in note


def test_regular_empty_note_aligns_verification_and_hides_internal_axis_math(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)

    note = formatter.build_market_note(
        original_report="검증: 로컬인사이트엔진 · 엄격 · 원문 1건 → 신규 1개 선별 · 중복억제 0건",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=datetime(2026, 10, 10, 8, 30, tzinfo=KST),
        market_context={"usd_krw": 1340.8},
        snapshot=_with_session_dates(_snapshot()),
        selected=[],
    )

    assert "새로 확인된 중요 변화 없음" in note
    assert "축1 지수(30%)" not in note
    assert "4축 판정" not in note
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



def test_regular_note_drops_unconfirmed_us_company_news_even_when_source_is_b(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    monkeypatch.setattr(
        formatter,
        "_score_grade",
        lambda cluster: (cluster.materiality_score_override, cluster.materiality_grade_override),
    )
    cluster = FakeCluster(
        "CVS Health 사업분할 추진",
        "CVS Health가 2개 독립 상장사 분리를 검토한다.",
        "이벤트",
        ["헬스케어"],
        [_symbol("CVS Health", "CVS")],
        79,
        "B",
        "https://example.com/cvs",
    )
    note = formatter.build_market_note(
        original_report="1) [79/B] CVS Health 사업분할 추진\n검증: 테스트",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=datetime(2026, 11, 4, 20, 48, tzinfo=KST),
        market_context={"kospi_change_pct": 0.2, "kosdaq_change_pct": 0.1},
        snapshot=_with_session_dates(_snapshot(), "2026-11-03"),
        selected=[cluster],
    )

    assert "CVS Health" not in note
    assert "새로 확인된 중요 변화 없음" in note


def test_regular_note_keeps_b_semiconductor_news_when_sector_price_confirms(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    monkeypatch.setattr(
        formatter,
        "_score_grade",
        lambda cluster: (cluster.materiality_score_override, cluster.materiality_grade_override),
    )
    snapshot = _with_session_dates(_snapshot(), "2026-11-03")
    snapshot["assets"]["^SOX"]["change_pct"] = 2.63
    cluster = FakeCluster(
        "반도체 장비, 3년 뒤 계약까지 줄섰다",
        "반도체 장비 수요와 수주잔고가 장기간 이어지고 있다는 내용이 확인됐다.",
        "이벤트",
        ["반도체"],
        [],
        79,
        "B",
        "https://example.com/semicap",
    )
    note = formatter.build_market_note(
        original_report="1) [79/B] 반도체 장비, 3년 뒤 계약까지 줄섰다\n검증: 테스트",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=datetime(2026, 11, 4, 20, 48, tzinfo=KST),
        market_context={"kospi_change_pct": 0.2, "kosdaq_change_pct": 0.1},
        snapshot=snapshot,
        selected=[cluster],
    )

    assert "반도체 장비, 3년 뒤 계약까지 줄섰다" in note
    assert "섹터 영향 확인 필요: 반도체" in note


def test_regular_note_excludes_invalid_qqq_and_surfaces_data_warning(monkeypatch):
    monkeypatch.setattr(formatter, "_ensure_note_assets", lambda snapshot: snapshot)
    snapshot = _with_session_dates(_snapshot(), "2026-11-03")
    snapshot["assets"]["QQQ"].update({
        "change_pct": -5.30,
        "validated_change_pct": None,
        "validation_status": "invalid",
        "validation_reason": "benchmark_divergence:^IXIC:6.13pp",
    })
    snapshot["validation_warnings"] = ["QQQ -5.30% vs ^IXIC +0.83% 괴리 6.13%p"]
    note = formatter.build_market_note(
        original_report="검증: 테스트",
        summaries=[],
        hours=1,
        timezone_name="Asia/Seoul",
        kind="regular",
        now=datetime(2026, 11, 4, 20, 48, tzinfo=KST),
        market_context={"kospi_change_pct": 0.2, "kosdaq_change_pct": 0.1},
        snapshot=snapshot,
        selected=[],
    )

    proxy_line = next(line for line in note.splitlines() if "ETF 가격 프록시" in line)
    assert "QQQ -5.30%" not in proxy_line
    assert "비정상 프록시 제외" in note
    assert "QQQ -5.30% vs ^IXIC +0.83% 괴리 6.13%p" in note
