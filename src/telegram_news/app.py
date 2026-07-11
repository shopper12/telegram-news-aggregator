from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from .settings import load_settings, load_channels
from .telegram_client import collect_messages
from .store import connect, init_db, insert_messages, fetch_recent
from .normalizer import deduplicate_rows
from .summarizer import gemini_classify_if_available
from .strict_report_v2 import build_markdown_report
from .kakao_notifier import send_kakao_memo
from .discord_notifier import send_discord_webhook
from .notifier import send_telegram_message_to_many
from .report_cache import save_latest_report


DEFAULT_KAKAO_WEB_URL = "https://github.com/shopper12/telegram-news-aggregator"
DEFAULT_NEWS_HOURS = int(os.getenv("NEWS_LOOKBACK_HOURS", "1"))
DEFAULT_NEWS_LIMIT = int(os.getenv("NEWS_CLASSIFY_LIMIT", "999"))
DEFAULT_COLLECT_LIMIT = int(os.getenv("NEWS_COLLECT_LIMIT_PER_CHANNEL", "200"))


def _auto_briefing_kind() -> tuple[str, int, int]:
    """Return (briefing_kind, hours, limit) from current KST hour."""
    now_h = datetime.now(ZoneInfo("Asia/Seoul")).hour
    if 8 <= now_h < 9:
        return "premarket", DEFAULT_NEWS_HOURS, DEFAULT_NEWS_LIMIT
    if 9 <= now_h < 15:
        return "intraday", DEFAULT_NEWS_HOURS, DEFAULT_NEWS_LIMIT
    if 15 <= now_h < 17:
        return "aftermarket", DEFAULT_NEWS_HOURS, DEFAULT_NEWS_LIMIT
    return "overnight", DEFAULT_NEWS_HOURS, DEFAULT_NEWS_LIMIT


def _resolve_window(args: argparse.Namespace, *, default_hours: int, default_limit: int, set_kind: bool) -> tuple[int, int]:
    if set_kind and not os.getenv("BRIEFING_KIND"):
        kind, auto_hours, auto_limit = _auto_briefing_kind()
        os.environ["BRIEFING_KIND"] = kind
    else:
        auto_hours, auto_limit = default_hours, default_limit

    hours = args.hours if args.hours is not None else auto_hours
    limit = args.limit if args.limit is not None else auto_limit
    return hours, limit


def cmd_init_db(args: argparse.Namespace) -> None:
    settings = load_settings()
    conn = connect(settings.database_path)
    init_db(conn)
    print(f"DB initialized: {settings.database_path}")


def cmd_collect(args: argparse.Namespace) -> None:
    settings = load_settings()
    channels = load_channels(settings.channel_config_path)

    if not channels:
        raise RuntimeError("No valid channels found. Edit config/channels.yaml first.")

    hours, limit = _resolve_window(args, default_hours=DEFAULT_NEWS_HOURS, default_limit=DEFAULT_COLLECT_LIMIT, set_kind=False)

    conn = connect(settings.database_path)
    init_db(conn)

    messages = asyncio.run(
        collect_messages(
            settings=settings,
            channels=channels,
            hours=hours,
            limit_per_channel=limit,
        )
    )
    inserted = insert_messages(conn, messages)
    print(f"Collected={len(messages)}, inserted={inserted}")


def _make_report(hours: int, limit: int) -> str:
    settings = load_settings()
    conn = connect(settings.database_path)
    init_db(conn)

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = fetch_recent(conn, since)
    deduped = deduplicate_rows(rows)
    summaries = gemini_classify_if_available(
        deduped,
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        limit=limit,
    )
    return build_markdown_report(summaries, hours=hours, timezone_name=settings.timezone)


def _save_report(report: str) -> Path:
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"telegram_news_{stamp}.md"
    path.write_text(report, encoding="utf-8")
    return path


def _is_empty_report(report: str) -> bool:
    return not report or not report.strip()


def _send_report_to_telegram(report: str) -> None:
    settings = load_settings()
    bot_token = settings.telegram_bot_token
    chat_ids = settings.telegram_target_chat_ids
    if not bot_token or not chat_ids:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_TARGET_CHAT_ID(S) are required for Telegram notification.")
    send_telegram_message_to_many(bot_token=bot_token, chat_ids=chat_ids, text=report)


def _send_report_to_kakao(report: str) -> None:
    rest_api_key = os.getenv("KAKAO_REST_API_KEY")
    refresh_token = os.getenv("KAKAO_REFRESH_TOKEN")
    client_secret = os.getenv("KAKAO_CLIENT_SECRET") or None
    web_url = os.getenv("KAKAO_WEB_URL") or DEFAULT_KAKAO_WEB_URL

    if not rest_api_key or not refresh_token:
        raise RuntimeError("KAKAO_REST_API_KEY and KAKAO_REFRESH_TOKEN are required for Kakao notification.")

    rotated_refresh_token = send_kakao_memo(
        rest_api_key=rest_api_key,
        refresh_token=refresh_token,
        client_secret=client_secret,
        text=report,
        web_url=web_url,
    )
    print("Sent to KakaoTalk memo")
    if rotated_refresh_token:
        print("Kakao returned a rotated refresh token. Update the KAKAO_REFRESH_TOKEN GitHub secret with the new value shown below.")
        print(rotated_refresh_token)


def _send_report_to_discord(report: str) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    username = os.getenv("DISCORD_WEBHOOK_USERNAME") or "뉴스봇"
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is required for Discord notification.")
    send_discord_webhook(webhook_url=webhook_url, text=report, username=username)
    print("Sent to Discord webhook")


def _send_report(report: str) -> None:
    notifier = (os.getenv("NOTIFIER") or "none").strip().lower()
    if notifier in {"", "none", "off", "false", "no"}:
        print("Notifier disabled; report cache was still generated and saved.")
        return
    if notifier not in {"telegram", "kakao", "discord", "both", "all"}:
        raise RuntimeError("NOTIFIER must be one of: none, telegram, kakao, discord, both, all")

    if notifier in {"telegram", "all"}:
        _send_report_to_telegram(report)
    if notifier in {"kakao", "both", "all"}:
        _send_report_to_kakao(report)
    if notifier in {"discord", "both", "all"}:
        _send_report_to_discord(report)


def _finalize_report(*, report: str, kind: str, hours: int, source: str, send: bool) -> Path:
    path = _save_report(report)
    save_latest_report(report=report, kind=kind, hours=hours, source=source)
    print(report)
    print(f"\nSaved: {path}")
    if send:
        _send_report(report)
    return path


def generate_report(
    *,
    hours: int = 1,
    limit: int = DEFAULT_NEWS_LIMIT,
    briefing_kind: str = "manual",
    collect: bool = True,
    send: bool = False,
    source: str = "telegram_manual",
) -> str:
    previous_kind = os.environ.get("BRIEFING_KIND")
    os.environ["BRIEFING_KIND"] = briefing_kind
    try:
        if collect:
            collect_args = argparse.Namespace(hours=hours, limit=DEFAULT_COLLECT_LIMIT)
            cmd_collect(collect_args)
        report = _make_report(hours=hours, limit=limit)
        if _is_empty_report(report):
            return "리포트 생성 결과가 비어 있습니다. 수집 채널, 시간 범위, 필터 조건을 확인하세요."
        _finalize_report(report=report, kind=briefing_kind, hours=hours, source=source, send=send)
        return report
    finally:
        if previous_kind is None:
            os.environ.pop("BRIEFING_KIND", None)
        else:
            os.environ["BRIEFING_KIND"] = previous_kind


def cmd_report(args: argparse.Namespace) -> None:
    hours, limit = _resolve_window(args, default_hours=DEFAULT_NEWS_HOURS, default_limit=DEFAULT_NEWS_LIMIT, set_kind=True)
    report = _make_report(hours=hours, limit=limit)
    if _is_empty_report(report):
        print("Report skipped: empty report generated. SEND_EMPTY_REPORT=0 is active or no reportable issue exists.")
        return

    kind = os.getenv("BRIEFING_KIND", "regular")
    _finalize_report(report=report, kind=kind, hours=hours, source="cli_report", send=getattr(args, "send", False))


def cmd_run(args: argparse.Namespace) -> None:
    hours, limit = _resolve_window(args, default_hours=DEFAULT_NEWS_HOURS, default_limit=DEFAULT_NEWS_LIMIT, set_kind=True)
    args.hours = hours
    args.limit = DEFAULT_COLLECT_LIMIT if args.limit is None else args.limit
    cmd_collect(args)
    report = _make_report(hours=hours, limit=limit)
    if _is_empty_report(report):
        print("Report skipped: empty report generated. Notification skipped.")
        return

    kind = os.getenv("BRIEFING_KIND", "regular")
    _finalize_report(report=report, kind=kind, hours=hours, source="cli_run", send=args.send)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram News Aggregator")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("collect")
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("report")
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--send", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("run")
    p.add_argument("--hours", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--send", action="store_true")
    p.set_defaults(func=cmd_run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
