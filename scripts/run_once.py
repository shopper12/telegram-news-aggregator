from telegram_news.consistency_rules import install as install_consistency_rules
from telegram_news.continuous_quote_fallback import install as install_continuous_quotes
from telegram_news.market_note_formatter import install_dispatch_hook
from telegram_news.morning_brief_config import install_atomic_assets as install_morning_brief_assets
from telegram_news.official_quote_guard import install as install_official_quote_guard
from telegram_news.report_integrity import install_dispatch_hook as install_report_integrity_hook
from telegram_news.telegram_dispatch import main


if __name__ == "__main__":
    install_continuous_quotes()
    install_official_quote_guard()
    install_consistency_rules()
    install_dispatch_hook()
    install_morning_brief_assets()
    install_report_integrity_hook()
    main()
