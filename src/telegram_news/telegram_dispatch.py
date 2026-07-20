from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any

from .notifier import send_telegram_message_to_many
from .report_cache import LATEST_REPORT_JSON, load_latest_report
from .settings import load_settings


FALSE_VALUES = {"0", "false", "off", "no", "disabled"}


def _telegram_enabled() -> bool:
    return str(os.getenv("TELEGRAM_SEND_ENABLED", "1")).strip().lower() not in FALSE_VALUES


def _report_hash(report_text: str) -> str:
    normalized = report_text.replace("\r\n", "\n").strip()
    return sha256(normalized.encode("utf-8")).hexdigest()


def _read_raw_latest_payload() -> dict[str, Any]:
    if not LATEST_REPORT_JSON.exists():
        return {}
    try:
        data = json.loads(LATEST_REPORT_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"[telegram-dispatch] latest cache metadata read failed: {type(exc).__name__}: {exc}")
        return {}


def _last_dispatched_hash() -> str:
    payload = _read_raw_latest_payload()
    state = payload.get("telegram_dispatch")
    if not isinstance(state, dict):
        return ""
    return str(state.get("report_hash") or "").strip()


def _mark_dispatched(report_hash: str, chat_count: int) -> None:
    payload = _read_raw_latest_payload()
    if not payload:
        payload = load_latest_report()
    payload["telegram_dispatch"] = {
        "report_hash": report_hash,
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "chat_count": chat_count,
        "status": "sent",
    }
    LATEST_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    LATEST_REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dispatch_latest_report_to_telegram(
    report_text: str | None = None,
    *,
    previous_hash: str | None = None,
    force: bool = False,
    raise_on_error: bool = False,
) -> bool:
    """Send the report currently stored by report_cache to Telegram.

    The cache is the source of truth. ``report_text`` is accepted only for
    consistency checking; the actual outbound body is reloaded from
    ``load_latest_report()`` after generation and cache persistence.
    """
    if not _telegram_enabled():
        print("[telegram-dispatch] disabled by TELEGRAM_SEND_ENABLED=0")
        return False

    try:
        payload = load_latest_report()
        cached_report = str(payload.get("report") or "").strip()
        if not cached_report:
            raise RuntimeError("reports/latest_report.json has no report text")

        if report_text and report_text.strip() != cached_report:
            print("[telegram-dispatch] generated text differs from cache; cached report will be sent")

        current_hash = _report_hash(cached_report)
        last_hash = (previous_hash or _last_dispatched_hash()).strip()
        if not force and last_hash and current_hash == last_hash:
            print(f"[telegram-dispatch] duplicate skipped: sha256={current_hash}")
            return False

        settings = load_settings()
        token = str(settings.telegram_bot_token or "").strip()
        chat_ids = [str(value).strip() for value in settings.telegram_target_chat_ids if str(value).strip()]
        missing: list[str] = []
        if not token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat_ids:
            missing.append("TELEGRAM_TARGET_CHAT_ID or TELEGRAM_TARGET_CHAT_IDS")
        if missing:
            raise RuntimeError("missing Telegram configuration: " + ", ".join(missing))

        send_telegram_message_to_many(bot_token=token, chat_ids=chat_ids, text=cached_report)
        _mark_dispatched(current_hash, len(chat_ids))
        print(f"[telegram-dispatch] sent: chats={len(chat_ids)} sha256={current_hash}")
        return True
    except Exception as exc:
        print(f"[telegram-dispatch] failed: {type(exc).__name__}: {exc}")
        if raise_on_error:
            raise
        return False


def generate_and_send_latest_report(
    *,
    hours: int = 1,
    limit: int = 999,
    briefing_kind: str = "manual",
    collect: bool = True,
    source: str = "telegram_manual",
    force_send: bool = False,
) -> str:
    """Collect, generate, cache, dispatch, and return one Telegram report."""
    from .unified_pipeline import apply_unified_pipeline

    apply_unified_pipeline()
    previous_hash = _last_dispatched_hash()

    from .app import generate_report

    report = generate_report(
        hours=hours,
        limit=limit,
        briefing_kind=briefing_kind,
        collect=collect,
        send=False,
        source=source,
    )
    if report.startswith("리포트 생성 결과가 비어"):
        print("[telegram-dispatch] generation returned an empty-report notice; send skipped")
        return report

    dispatch_latest_report_to_telegram(
        report,
        previous_hash=previous_hash,
        force=force_send,
        raise_on_error=False,
    )
    return report


def _install_cli_dispatch_hook(previous_hash: str) -> None:
    from . import app

    def _restored_send(report: str) -> None:
        dispatch_latest_report_to_telegram(
            report,
            previous_hash=previous_hash,
            raise_on_error=True,
        )

        notifier = str(os.getenv("NOTIFIER") or "none").strip().lower()
        if notifier not in {"none", "", "off", "false", "no", "telegram", "kakao", "discord", "both", "all"}:
            raise RuntimeError("NOTIFIER must be one of: none, telegram, kakao, discord, both, all")
        if notifier in {"kakao", "both", "all"}:
            app._send_report_to_kakao(report)
        if notifier in {"discord", "both", "all"}:
            app._send_report_to_discord(report)

    app._send_report = _restored_send


def main() -> None:
    """CLI entry point used by GitHub Actions and local commands."""
    from .unified_pipeline import apply_unified_pipeline

    apply_unified_pipeline()
    previous_hash = _last_dispatched_hash()
    _install_cli_dispatch_hook(previous_hash)

    from .app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
