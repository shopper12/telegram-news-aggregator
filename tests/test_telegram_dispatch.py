import json

from telegram_news import telegram_dispatch as dispatch


def test_dispatch_reads_cache_and_blocks_duplicate(monkeypatch, tmp_path):
    latest_path = tmp_path / "latest_report.json"
    payload = {
        "ok": True,
        "generated_at": "2026-07-20T12:00:00",
        "source": "test",
        "report": "테스트 텔레그램 뉴스 리포트",
    }
    latest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(dispatch, "LATEST_REPORT_JSON", latest_path)
    monkeypatch.setattr(dispatch, "load_latest_report", lambda: json.loads(latest_path.read_text(encoding="utf-8")))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "100,200")
    monkeypatch.setenv("TELEGRAM_SEND_ENABLED", "1")

    calls = []

    def fake_send(*, bot_token, chat_ids, text):
        calls.append((bot_token, chat_ids, text))

    monkeypatch.setattr(dispatch, "send_telegram_message_to_many", fake_send)

    assert dispatch.dispatch_latest_report_to_telegram() is True
    assert len(calls) == 1
    assert calls[0][1] == ["100", "200"]

    saved = json.loads(latest_path.read_text(encoding="utf-8"))
    report_hash = saved["telegram_dispatch"]["report_hash"]
    assert report_hash

    assert dispatch.dispatch_latest_report_to_telegram() is False
    assert len(calls) == 1


def test_dispatch_logs_missing_configuration(monkeypatch, tmp_path, capsys):
    latest_path = tmp_path / "latest_report.json"
    latest_path.write_text(
        json.dumps({"ok": True, "report": "설정 누락 테스트 리포트"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(dispatch, "LATEST_REPORT_JSON", latest_path)
    monkeypatch.setattr(dispatch, "load_latest_report", lambda: json.loads(latest_path.read_text(encoding="utf-8")))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_SEND_ENABLED", "1")

    assert dispatch.dispatch_latest_report_to_telegram() is False
    output = capsys.readouterr().out
    assert "missing Telegram configuration" in output
    assert "TELEGRAM_BOT_TOKEN" in output
