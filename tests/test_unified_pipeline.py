from telegram_news.evidence_summarizer import summarize_article
from telegram_news.importance_selector import dedupe_articles, score_article, select_top_articles


def test_dedupe_articles_merges_tracking_url_variants():
    articles = [
        {
            "title": "삼성전자, AI 반도체 공급 계약 체결",
            "body": "삼성전자가 1조원 규모 공급 계약을 체결했다.",
            "source": "텔레그램채널A",
            "url": "https://example.com/news/1?utm_source=telegram",
            "age_minutes": 10,
            "market_impact_score": 90,
            "symbols": ["005930.KS"],
        },
        {
            "title": "삼성전자 AI 반도체 공급계약 체결",
            "body": "계약 규모는 1조원이다.",
            "source": "텔레그램채널B",
            "url": "https://example.com/news/1?utm_campaign=test",
            "age_minutes": 20,
            "market_impact_score": 80,
            "symbols": ["005930.KS"],
        },
    ]

    deduped = dedupe_articles(articles)

    assert len(deduped) == 1
    assert deduped[0]["duplicate_count"] == 2
    assert set(deduped[0]["duplicate_sources"]) == {"텔레그램채널A", "텔레그램채널B"}


def test_advertising_like_words_do_not_exclude_stock_relevant_message():
    stock_message = {
        "title": "무료 공개 삼성전자 HBM 공급 계약",
        "body": "삼성전자가 2조원 규모 HBM 공급 계약을 체결해 매출 증가가 예상된다.",
        "source": "내텔레그램",
        "symbols": ["005930.KS"],
        "market_impact_score": 92,
        "age_minutes": 5,
    }
    generic_message = {
        "title": "오늘 시장 이야기",
        "body": "시장 참여자들이 여러 의견을 나눴다.",
        "source": "내텔레그램",
        "market_impact_score": 20,
        "age_minutes": 5,
    }

    selected = select_top_articles([generic_message, stock_message], limit=2)

    assert selected[0]["title"] == stock_message["title"]
    assert selected[0]["selection_basis"] == "importance_and_stock_relevance"
    assert any(item["title"] == stock_message["title"] for item in selected)


def test_repetition_and_direct_symbol_raise_importance_score():
    direct_repeated = {
        "title": "SK하이닉스 HBM 공급 확대",
        "body": "SK하이닉스가 HBM 공급을 확대해 매출이 증가한다.",
        "symbols": ["000660.KS"],
        "market_impact_score": 80,
        "repeat_count": 4,
        "channel_count": 3,
        "age_minutes": 20,
    }
    generic = {
        "title": "반도체 업황 의견",
        "body": "반도체 시장에 대한 의견이 공유됐다.",
        "market_impact_score": 80,
        "age_minutes": 20,
    }

    assert score_article(direct_repeated) > score_article(generic)


def test_select_top_articles_uses_score_without_category_cap():
    articles = []
    for index in range(7):
        articles.append({
            "title": f"기업{index} 공급 계약 {index + 1}00억원",
            "body": f"기업{index}이 공급 계약을 체결해 매출 증가가 예상된다.",
            "source": "내텔레그램",
            "symbols": [f"A{index}"],
            "market_impact_score": 90 - index,
            "age_minutes": index * 5,
        })

    selected = select_top_articles(articles, limit=6)

    assert len(selected) == 6
    assert all(item["selection_category"] == "개별종목" for item in selected)


def test_summarize_article_uses_importance_without_phrase_exclusion():
    article = {
        "title": "무료 공개 한화시스템 1,200억원 방산 계약",
        "body": (
            "무료 공개: 한화시스템은 국방부와 1,200억원 규모의 방산 시스템 공급 계약을 체결했다. "
            "정부의 방산 수출 확대 정책이 계약 배경으로 작용했다."
        ),
        "source": "내텔레그램",
        "published_at": "2026-07-20T10:00:00+09:00",
    }

    summary = summarize_article(article)
    main_text = summary.split("\n출처:", 1)[0]

    assert "무료 공개" in main_text
    assert "1,200억원" in main_text
    assert "한화시스템" in main_text
    assert "배경" in main_text or "정책" in main_text
    assert "출처: 내텔레그램" in summary
