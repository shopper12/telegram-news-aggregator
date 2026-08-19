from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram_news import market_insight_watch as watch


KST = ZoneInfo("Asia/Seoul")


def _current_evidence(source_groups=None):
    groups = source_groups or [["reuters.com"], ["bloomberg.com"], ["wsj.com"]]
    return [
        {"id": "E1", "title": "AI 주도주 차익실현", "source_groups": groups[0]},
        {"id": "E2", "title": "바이오·에너지 상대강세", "source_groups": groups[1]},
        {"id": "E3", "title": "AI 실적 컨센서스 상향 유지", "source_groups": groups[2]},
    ]


def _market():
    return {
        "QQQ": {"ticker": "QQQ", "label": "나스닥100", "price": 620.0, "change_pct": -1.4, "return_5d": -2.0},
        "XBI": {"ticker": "XBI", "label": "나스닥바이오", "price": 130.0, "change_pct": 1.8, "return_5d": 4.0},
        "XLE": {"ticker": "XLE", "label": "에너지", "price": 105.0, "change_pct": 1.2, "return_5d": 3.5},
    }


def _candidate(score=90):
    return {
        "should_alert": True,
        "score": score,
        "confidence": "high",
        "thesis": "AI 펀더멘털 훼손보다 포지셔닝 피로가 먼저 나타나며 비AI 섹터로 순환매가 확산되는 국면",
        "chain": [
            "AI 주도주는 장기간 상승 뒤 차익실현이 확대",
            "실적 컨센서스는 아직 상향돼 가격과 펀더멘털이 분리",
            "바이오·에너지 등 대체 섹터가 상대강세",
            "따라서 전면 위험회피보다 고변동성 자금의 순환 성격이 강함",
        ],
        "conclusion": "AI 붕괴보다 포지셔닝 재배치와 섹터 순환을 우선 관찰해야 하는 구간",
        "why_now": "가격 약세와 실적 추세가 엇갈리기 시작했기 때문",
        "counterevidence": ["AI 실적 추정치 하향이 광범위하게 시작되면 무효화"],
        "watch_next": ["QQQ 대비 XBI·XLE 상대강도"],
        "evidence_refs": ["E1", "E2", "E3"],
        "new_trigger_refs": ["E1", "E2"],
        "market_confirmations": ["QQQ", "XBI", "XLE"],
        "theme_tags": ["AI포지셔닝피로", "섹터순환"],
    }


def test_rich_cross_signal_candidate_passes_gate(monkeypatch):
    monkeypatch.setattr(watch, "ALERT_SCORE", 82)
    now = datetime(2026, 8, 20, 5, 0, tzinfo=KST)

    gate = watch.evaluate_candidate_gate(_candidate(), _current_evidence(), [], _market(), {"alerts": []}, now)

    assert gate["eligible"] is True
    assert gate["reasons"] == []
    assert len(gate["source_groups"]) == 3
    assert gate["market_refs"] == ["QQQ", "XBI", "XLE"]


def test_low_score_single_headline_style_candidate_is_blocked(monkeypatch):
    monkeypatch.setattr(watch, "ALERT_SCORE", 82)
    now = datetime(2026, 8, 20, 5, 0, tzinfo=KST)
    candidate = _candidate(score=70)
    candidate["chain"] = ["한 종목이 급락함"]
    candidate["evidence_refs"] = ["E1"]
    candidate["new_trigger_refs"] = ["E1"]
    candidate["market_confirmations"] = ["QQQ"]

    gate = watch.evaluate_candidate_gate(candidate, _current_evidence(), [], _market(), {"alerts": []}, now)

    assert gate["eligible"] is False
    assert "score_below_82" in gate["reasons"]
    assert "causal_chain_too_short" in gate["reasons"]
    assert "insufficient_evidence_refs" in gate["reasons"]


def test_same_origin_reposts_do_not_count_as_independent_support(monkeypatch):
    monkeypatch.setattr(watch, "ALERT_SCORE", 82)
    now = datetime(2026, 8, 20, 5, 0, tzinfo=KST)
    same_source = _current_evidence([["t.me"], ["t.me"], ["t.me"]])

    gate = watch.evaluate_candidate_gate(_candidate(), same_source, [], _market(), {"alerts": []}, now)

    assert gate["eligible"] is False
    assert "insufficient_independent_sources" in gate["reasons"]


def test_same_thesis_is_suppressed_during_cooldown(monkeypatch):
    monkeypatch.setattr(watch, "ALERT_SCORE", 82)
    monkeypatch.setattr(watch, "COOLDOWN_HOURS", 12)
    now = datetime(2026, 8, 20, 5, 0, tzinfo=KST)
    candidate = _candidate(score=90)
    signature = watch._candidate_signature(candidate)
    state = {
        "alerts": [
            {
                "signature": signature,
                "fingerprint": "different",
                "score": 86,
                "alerted_at": (now - timedelta(hours=3)).isoformat(),
            }
        ]
    }

    gate = watch.evaluate_candidate_gate(candidate, _current_evidence(), [], _market(), state, now)

    assert gate["eligible"] is False
    assert "cooldown_same_thesis" in gate["reasons"]


def test_alert_renderer_keeps_insight_chain_conclusion_and_counterevidence():
    now = datetime(2026, 8, 20, 5, 0, tzinfo=KST)
    current = _current_evidence()
    candidate = _candidate()
    gate = watch.evaluate_candidate_gate(candidate, current, [], _market(), {"alerts": []}, now)
    evidence = {item["id"]: item for item in current}
    verification = {"passed": True, "sources": [{"uri": "a"}, {"uri": "b"}]}

    text = watch.render_alert(candidate, gate, verification, evidence, _market())

    assert text.startswith("🧠 중요 시장 인사이트 | 90점")
    assert "1. AI 주도주는" in text
    assert "결론." in text
    assert "반증/무효화 조건" in text
    assert "다음 확인 포인트" in text
    assert "대표 근거" in text
    assert "시장 확인" in text


def test_run_watch_sends_only_after_gate_and_google_verification(monkeypatch, tmp_path):
    now = datetime(2026, 8, 20, 5, 0, tzinfo=KST)
    current = _current_evidence()
    candidate = _candidate()
    monkeypatch.setattr(watch, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(watch, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(watch, "collect_news_summaries", lambda hours, limit: [])
    monkeypatch.setattr(watch, "build_current_evidence", lambda summaries: current)
    monkeypatch.setattr(watch, "build_memory_evidence", lambda when: [])
    monkeypatch.setattr(watch, "collect_market_confirmation", _market)
    monkeypatch.setattr(watch, "synthesize_candidate", lambda *args: (candidate, "test-engine"))
    monkeypatch.setattr(
        watch,
        "verify_candidate_with_google",
        lambda *args: {"passed": True, "reason": "pass", "sources": [{"uri": "a"}, {"uri": "b"}]},
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT_ID", "999")
    sent = []
    monkeypatch.setattr(watch, "send_telegram_message_to_many", lambda **kwargs: sent.append(kwargs))

    result = watch.run_watch(hours=6, limit=180, send=True, now=now)

    assert result["would_alert"] is True
    assert result["sent"] is True
    assert len(sent) == 1
    state = watch._load_json(watch.STATE_PATH, {})
    assert len(state["alerts"]) == 1
    assert watch.LATEST_PATH.exists()


def test_run_watch_does_not_verify_or_send_when_gate_fails(monkeypatch, tmp_path):
    now = datetime(2026, 8, 20, 5, 0, tzinfo=KST)
    candidate = _candidate(score=60)
    monkeypatch.setattr(watch, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(watch, "LATEST_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(watch, "collect_news_summaries", lambda hours, limit: [])
    monkeypatch.setattr(watch, "build_current_evidence", lambda summaries: _current_evidence())
    monkeypatch.setattr(watch, "build_memory_evidence", lambda when: [])
    monkeypatch.setattr(watch, "collect_market_confirmation", _market)
    monkeypatch.setattr(watch, "synthesize_candidate", lambda *args: (candidate, "test-engine"))
    monkeypatch.setattr(
        watch,
        "verify_candidate_with_google",
        lambda *args: (_ for _ in ()).throw(AssertionError("verification must not run")),
    )
    monkeypatch.setattr(
        watch,
        "send_telegram_message_to_many",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("send must not run")),
    )

    result = watch.run_watch(hours=6, limit=180, send=True, now=now)

    assert result["would_alert"] is False
    assert result["sent"] is False
    assert "score_below_82" in result["gate"]["reasons"]
