from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from .settings import load_settings, load_channels
from .telegram_client import collect_messages
from .store import connect, init_db, insert_messages, fetch_recent
from .normalizer import deduplicate_rows
from .summarizer import gemini_classify_if_available
from .strict_report_v2 import build_markdown_report
from .kakao_notifier import send_kakao_memo


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


def _save_report(report: str) -> Path:
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"telegram_news_{stamp}.md"
    path.write_text(report, encoding="utf-8")
    return path


def _is_empty_report(report: str) -> bool:
    return not report or not report.strip()


def _send_report_to_kakao(report: str) -> None:
    rest_api_key = os.getenv("KAKAO_REST_API_KEY")
    refresh_token = os.getenv("KAKAO_REFRESH_TOKEN")
    client_secret = os.getenv("KAKAO_CLIENT_SECRET") or None
    web_url = os.getenv("KAKAO_WEB_URL", "https://github.com/shopper12/telegram-news-aggregator")

    if not rest_api_key or not refresh_token:
        raise RuntimeError("KAKAO_REST_API_KEY and KAKAO_REFRESH_TOKEN are required for --send.")

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


def cmd_report(args: argparse.Namespace) -> None:
    report = _make_report(hours=args.hours, limit=args.limit)
    if _is_empty_report(report):
        print("Report skipped: empty report generated. SEND_EMPTY_REPORT=0 is active or no reportable issue exists.")
        return

    path = _save_report(report)
    print(report)
    print(f"\nSaved: {path}")


def cmd_run(args: argparse.Namespace) -> None:
    cmd_collect(args)
    report = _make_report(hours=args.hours, limit=args.limit)
    if _is_empty_report(report):
        print("Report skipped: empty report generated. KakaoTalk send skipped.")
        return

    path = _save_report(report)
    print(report)
    print(f"\nSaved: {path}")

    if args.send:
        _send_report_to_kakao(report)


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
