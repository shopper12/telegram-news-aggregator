from telegram_news.consistency_rules import install as install_consistency_rules
from telegram_news.telegram_dispatch import main


if __name__ == "__main__":
    install_consistency_rules()
    main()
