from telegram_news.unified_pipeline import dedupe_articles, select_top_articles, summarize_article


def test_dedupe_articles_merges_tracking_url_variants():
    articles = [
        {
            "title": "삼성전자, AI 반도체 공급 계약 체결",
            "body": "삼성전자가 1조원 규모 공급 계약을 체결했다.",
            "source": "연합뉴스",
            "url": "https://example.com/news/1?utm_source=telegram",
            "age_minutes": 10,
            "market_impact_score": 90,
        },
        {
            "title": "삼성전자 AI 반도체 공급계약 체결",
            "body": "계약 규모는 1조원이다.",
            "source": "한국경제",
            "url": "https://example.com/news/1?utm_campaign=test",
            "age_minutes": 20,
            "market_impact_score": 80,
        },
    ]

    deduped = dedupe_articles(articles)

    assert len(deduped) == 1
    assert deduped[0]["duplicate_count"] == 2
    assert set(deduped[0]["duplicate_sources"]) == {"연합뉴스", "한국경제"}


def test_select_top_articles_limits_category_concentration():
    articles = []
    for index in range(7):
        articles.append({
            "title": f"기업{index} 공급 계약 {index + 1}00억원",
            "body": f"기업{index}이 공급 계약을 체결해 매출 증가가 예상된다.",
            "source": "전자공시",
            "symbols": [f"A{index}"],
            "market_impact_score": 85 - index,
            "age_minutes": index * 5,
        })
    for index in range(4):
        articles.append({
            "title": f"연준 금리 정책 변화 {index}",
            "body": "연준이 금리 경로를 조정해 국채와 환율에 영향을 줄 수 있다.",
            "source": "로이터",
            "category": "거시",
            "market_impact_score": 80 - index,
            "age_minutes": 15 + index,
        })
    for index in range(3):
        articles.append({
            "title": f"정부 산업 정책 개편안 {index}",
            "body": "정부가 산업 정책 개편안을 발표해 기업 규제가 변경된다.",
            "source": "연합뉴스",
            "category": "정치/정책",
            "market_impact_score": 78 - index,
            "age_minutes": 20 + index,
        })

    selected = select_top_articles(articles, limit=10)
    categories = [item["selection_category"] for item in selected]

    assert len(selected) <= 10
    assert categories.count("개별종목") <= 4
    assert len(set(categories)) >= 3


def test_summarize_article_prefers_facts_and_omits_vague_sentence():
    article = {
        "title": "한화시스템, 1,200억원 방산 계약 체결",
        "body": (
            "한화시스템은 국방부와 1,200억원 규모의 방산 시스템 공급 계약을 체결했다. "
            "계약 기간은 2028년까지이며 수주잔고가 증가한다. "
            "정부의 방산 수출 확대 정책이 계약 배경으로 작용했다. "
            "향후 행보가 주목된다."
        ),
        "source": "전자공시",
        "published_at": "2026-07-20T10:00:00+09:00",
    }

    summary = summarize_article(article)
    main_text = summary.split("\n출처:", 1)[0]

    assert "1,200억원" in main_text
    assert "한화시스템" in main_text
    assert "배경" in main_text or "정책" in main_text
    assert "향후 행보가 주목된다" not in main_text
    assert "출처: 전자공시" in summary
