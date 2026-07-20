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

    # Simulate save_latest_report() overwriting dispatch metadata while producing
    # exactly the same report text on the next scheduled/manual run.
    regenerated = dict(payload)
    regenerated["generated_at"] = "2026-07-20T12:05:00"
    latest_path.write_text(json.dumps(regenerated, ensure_ascii=False), encoding="utf-8")

    assert dispatch.dispatch_latest_report_to_telegram(previous_hash=report_hash) is False
    assert len(calls) == 1

    regenerated_state = json.loads(latest_path.read_text(encoding="utf-8"))["telegram_dispatch"]
    assert regenerated_state["report_hash"] == report_hash
    assert regenerated_state["status"] == "duplicate_skipped"

    # The copied hash must also protect the following invocation without an
    # explicitly supplied previous_hash.
    assert dispatch.dispatch_latest_report_to_telegram() is False
    assert len(calls) == 1


def test_dispatch_uses_webhook_origin_chat_only(monkeypatch, tmp_path):
    latest_path = tmp_path / "latest_report.json"
    latest_path.write_text(
        json.dumps({"ok": True, "report": "웹훅 채팅 대상 테스트"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch, "LATEST_REPORT_JSON", latest_path)
    monkeypatch.setattr(dispatch, "load_latest_report", lambda: json.loads(latest_path.read_text(encoding="utf-8")))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT_IDS", "100,200")
    monkeypatch.setenv("TELEGRAM_SEND_ENABLED", "1")

    calls = []
    monkeypatch.setattr(
        dispatch,
        "send_telegram_message_to_many",
        lambda **kwargs: calls.append(kwargs),
    )

    assert dispatch.dispatch_latest_report_to_telegram(
        force=True,
        target_chat_ids=["999", "999"],
    ) is True
    assert calls[0]["chat_ids"] == ["999"]


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
