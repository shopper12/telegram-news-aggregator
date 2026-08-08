from telegram_news.consistency_rules import install as install_consistency_rules
from telegram_news.continuous_quote_fallback import install as install_continuous_quotes
from telegram_news.market_note_formatter import install_dispatch_hook
from telegram_news.telegram_dispatch import main


if __name__ == "__main__":
    install_continuous_quotes()
    install_consistency_rules()
    install_dispatch_hook()
    main()
