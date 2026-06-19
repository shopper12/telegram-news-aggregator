from __future__ import annotations

import os

# Keep /reply news output on the Telegram-collected report path.
# The legacy messenger endpoint checks LIVE_NEWS_QUERY before falling back to
# reports/latest_report.json; an impossible query makes that live headline path
# return empty and restores the Telegram summary behavior.
os.environ.setdefault(
    "LIVE_NEWS_QUERY",
    "__telegram_source_only_cache_report_9f8a7b6c5d4e3f2a1b0c__",
)
