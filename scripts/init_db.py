from telegram_news.app import main

if __name__ == "__main__":
    import sys
    sys.argv = ["telegram-news", "init-db"]
    main()
