# Market Crisis Watch

Adds an intraday shock detector to the existing Telegram news bot, without modifying the existing news report jobs.

## Notifications
- US SPY / QQQ / SOXX / IWM / NVDA / TSLA and KR KOSPI / KOSDAQ / Samsung Electronics / SK Hynix.
- Also observes GLD, TLT, HYG and EWY as context.
- Computes previous-close, 5-minute and 15-minute percentage changes from Yahoo Finance 1-minute bars.
- Skips after-hours or stale quotes (default: >420 seconds old), preventing daily close prices being represented as live.
- Two benchmarks moving together escalate severity; increases in urgency bypass cooldown.
- Telegram messages include observation timestamp, quote age, threshold and portfolio-risk checklist.
- Only event metadata and notification cooldown are persisted in a GitHub-committed JSON file.
- Existing Telegram webhook can reply to `봇 시장`, `봇 시장 왜 떨어져?`, `봇 시장 지금 팔아?`.

## Fallback (no dedicated server)
GitHub Actions `crisis-watch.yml` checks on a 5-minute schedule while markets are open. GitHub cron execution is best-effort, can be delayed or dropped, and does NOT guarantee 5-minute delivery. In addition, Yahoo Finance is unofficial and may be 15+ minutes delayed or throttled; a quote older than 420 seconds is rejected (not called real-time).

The existing repository GitHub secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_TARGET_CHAT_ID` or `TELEGRAM_TARGET_CHAT_IDS` are reused. Avoid posting secrets or tokens in source.

## Always-on mode
Run on an always-on VM or paid worker to request data every 60 seconds:
```bash
PYTHONPATH=src python scripts/run_crisis_watch.py --loop --interval 60
```
This request interval is not a promise of live/exchange quote freshness. For reliable alerts within seconds, replace `fetch_quote` with licensed exchange/broker real-time WebSocket data and add trading holiday/session-calendar validation. Render Free spins down after idle periods and should NOT host a mission-critical permanent monitor.

## Local verification
```bash
python -m pip install requests pytest
PYTHONPATH=src python -m pytest tests/test_crisis_watch.py
PYTHONPATH=src python scripts/run_crisis_watch.py --dry-run
```

## Security and limitations
- No automatic buying, selling or portfolio access.
- Telegram responses are sent only for requests from allowed recipient chat IDs when set.
- Gemini may optionally generate grounded context answers if `GEMINI_API_KEY` is configured; otherwise rule-based market status returns.
- Yahoo intraday is not a licensed real-time market data feed. Alert coverage and accuracy must be verified in production.
