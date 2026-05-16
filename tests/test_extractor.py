from telegram_news.extractor import extract_signals


def test_extract_sectors():
    sig = extract_signals("SMR 원전 수주와 변압기 전력기기 관련 뉴스")
    assert "원전" in sig.sectors
    assert "전력기기" in sig.sectors
    assert sig.importance_score > 0
