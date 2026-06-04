from telegram_news.extractor import extract_signals, market_type_from_categories


def test_extract_sectors():
    sig = extract_signals("SMR 원전 수주와 변압기 전력기기 관련 뉴스")
    assert "원전" in sig.sectors
    assert "전력기기" in sig.sectors
    assert sig.importance_score > 0


def test_extract_signals_expands_kr_sectors_and_action_score():
    sig = extract_signals("셀트리온 임상3상 승인 공급", repeat_count=1, market_type="KR")
    assert "바이오" in sig.sectors
    assert sig.importance_score >= 10


def test_extract_signals_supports_us_big_tech():
    sig = extract_signals("엔비디아 NVDA 데이터센터 GPU 수요 확대", repeat_count=1, market_type="US")
    assert "미국빅테크" in sig.sectors
    assert "NVDA" in sig.tickers


def test_repeated_news_gets_saturation_penalty():
    once = extract_signals("삼성전자 공급", repeat_count=1, market_type="KR")
    repeated = extract_signals("삼성전자 공급", repeat_count=3, market_type="KR")
    assert repeated.importance_score < once.importance_score + 6


def test_market_type_from_categories():
    assert market_type_from_categories(["us_stock"]) == "US"
    assert market_type_from_categories(["crypto"]) == "CRYPTO"
    assert market_type_from_categories(["korea_stock"]) == "KR"
