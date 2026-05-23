from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .settings import load_settings, load_channels
from .telegram_client import collect_messages
from .store import connect, init_db, insert_messages, fetch_recent
from .normalizer import deduplicate_rows
from .summarizer import gemini_classify_if_available
from .strict_report_v2 import build_markdown_report
from .notifier import send_telegram_message_to_many


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

    conn = connect(settings.database_path)
    init_db(conn)

    messages = asyncio.run(
        collect_messages(
            settings=settings,
            channels=channels,
            hours=args.hours,
            limit_per_channel=args.limit,
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


def cmd_report(args: argparse.Namespace) -> None:
    report = _make_report(hours=args.hours, limit=args.limit)
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"telegram_news_{stamp}.md"
    path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\nSaved: {path}")


def cmd_run(args: argparse.Namespace) -> None:
    cmd_collect(args)
    report = _make_report(hours=args.hours, limit=args.limit)

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"telegram_news_{stamp}.md"
    path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\nSaved: {path}")

    if args.send:
        settings = load_settings()
        if not settings.telegram_bot_token or not settings.telegram_target_chat_ids:
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_TARGET_CHAT_ID(S) are required for --send.")
        send_telegram_message_to_many(settings.telegram_bot_token, settings.telegram_target_chat_ids, report)
        print(f"Sent to Telegram recipients: {len(settings.telegram_target_chat_ids)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram News Aggregator")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("collect")
    p.add_argument("--hours", type=int, default=6)
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("report")
    p.add_argument("--hours", type=int, default=6)
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("run")
    p.add_argument("--hours", type=int, default=6)
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--send", action="store_true")
    p.set_defaults(func=cmd_run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
