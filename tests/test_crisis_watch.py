from datetime import datetime, timedelta, timezone

from telegram_news.crisis_watch import (
    _lagged_return, detect_events, format_alert, is_open, should_send, _level
)
from telegram_news.crisis_chat import should_handle

UTC = timezone.utc

def _row(symbol, day=-3.0, m5=-1.1, m15=-2.0, fresh=True, opened=True, kind="index"):
    return {"symbol": symbol, "name": symbol, "region": "US",
            "kind": kind, "price": 100.0, "fresh": fresh, "open": opened,
            "age_sec": 60, "day_pct": day, "m5_pct": m5, "m15_pct": m15}

def test_session_hours():
    assert is_open("US", datetime(2026, 11, 5, 19, 0, tzinfo=UTC))
    assert not is_open("US", datetime(2026, 11, 5, 23, 0, tzinfo=UTC))
    assert is_open("KR", datetime(2026, 11, 5, 3, 0, tzinfo=UTC))
    assert not is_open("KR", datetime(2026, 11, 7, 3, 0, tzinfo=UTC))

def test_five_minute_lookback():
    bars = [(1000+i*60, 100.0-i) for i in range(16)]
    assert _lagged_return(bars, 300) == round((85/90-1)*100, 3)
    assert _lagged_return(bars[:2], 900) is None

def test_stale_and_closed_are_rejected():
    assert _level(_row("SPY", day=-10, fresh=False))[0] == 0
    assert _level(_row("SPY", day=-10, opened=False))[0] == 0

def test_breadth_escalation_and_dedup():
    snapshot = {"checked_at": "2026-11-06T04:00:00+09:00",
                "quotes": {"SPY": _row("SPY"), "QQQ": _row("QQQ"),
                           "NVDA": _row("NVDA", day=-9, m5=-5.1, m15=-7.1, kind="stock")}}
    events = detect_events(snapshot)
    assert events
    assert any(e["key"] == "US-하락" and e["level"] == 3 for e in events)
    assert "자료" in format_alert(events[0], snapshot)
    event = next(x for x in events if x["key"] == "US-하락")
    now = datetime(2026, 11, 5, 19, 0, tzinfo=UTC)
    state = {"sent": {event["key"]: {"level": 3, "at": now.isoformat()}}}
    assert not should_send(event, state, now + timedelta(minutes=20))
    assert should_send(event, state, now + timedelta(minutes=50))

def test_escalation_breaks_cooldown():
    now = datetime(2026, 11, 5, 19, 0, tzinfo=UTC)
    event = {"key": "US-하락", "level": 3}
    state = {"sent": {"US-하락": {"level": 2, "at": now.isoformat()}}}
    assert should_send(event, state, now + timedelta(minutes=1))

def test_conversational_router_does_not_override_legacy_news():
    assert should_handle("봇 시장 지금 팔아?")
    assert should_handle("왜 떨어져? 오늘 나스닥 폭락")
    assert should_handle("/risk")
    assert not should_handle("봇 뉴스")
    assert not should_handle("봇 시세 NVDA")
