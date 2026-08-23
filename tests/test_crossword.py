from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from telegram_news.crossword.generator import BANKS, generate_puzzle, normalize_answer
from telegram_news.crossword.integration import KAKAO_QUICK_MENU, _attach_quick_menu, _mention_to_command, install
from telegram_news.crossword.service import CrosswordService, _hint_reveals_answer
from telegram_news.crossword.storage import CrosswordStore


def test_generator_one_year_two_languages():
    start = date(2026, 1, 1)
    for offset in range(365):
        day = (start + timedelta(days=offset)).isoformat()
        for lang in ("ko", "en"):
            public, solution = generate_puzzle(lang, day)
            assert len(public["entries"]) >= 5
            assert len(solution["answers"]) == len(public["entries"])
            assert public["difficulty"] == "high-school-plus"
            assert public["clueStyle"] == "contextual-indirect"
            serialized = str(public)
            for answer in solution["answers"].values():
                assert answer not in serialized


def test_crossword_bank_is_high_school_plus_and_not_translation_drill():
    legacy_easy_ko = {"학교", "학생", "사과", "기차", "시장"}
    legacy_easy_en = {"APPLE", "WATER", "HOUSE", "CHAIR", "SCHOOL"}
    assert legacy_easy_ko.isdisjoint({answer for answer, _ in BANKS["ko"]})
    assert legacy_easy_en.isdisjoint({answer for answer, _ in BANKS["en"]})
    assert len(BANKS["ko"]) >= 50
    assert len(BANKS["en"]) >= 50
    assert all(len(clue) >= 18 for _, clue in BANKS["ko"])
    assert all(len(clue) >= 34 and not any("가" <= char <= "힣" for char in clue) for _, clue in BANKS["en"])


def test_normalize_answers_composes_hangul_compatibility_jamo():
    assert normalize_answer(" apple ", "en") == "APPLE"
    assert normalize_answer(" 사 과 ", "ko") == "사과"
    assert normalize_answer("ㅎㅏㄴ", "ko") == "한"


def test_direct_friend_hint_spoilers_are_rejected():
    assert _hint_reveals_answer("정답은 역설이야", "역설", "ko") is True
    assert _hint_reveals_answer("첫 글자는 역이야", "역설", "ko") is True
    assert _hint_reveals_answer("It starts with A", "AMBIGUOUS", "en") is True
    assert _hint_reveals_answer("한 표현이 겉으로 모순돼 보여도 의미가 더 선명해지는 경우를 떠올려 봐", "역설", "ko") is False


def test_requester_only_hint_and_friendship(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSWORD_SQLITE_PATH", str(tmp_path / "crossword.db"))
    monkeypatch.setenv("CROSSWORD_SIGNING_SECRET", "x" * 40)
    monkeypatch.setenv("CROSSWORD_PUBLIC_BASE_URL", "https://example.test")
    store = CrosswordStore("")
    service = CrosswordService(store)
    requester, req_token = service.session_for_platform_user("kakao:requester")
    helper, helper_token = service.session_for_platform_user("kakao:helper")
    service.publish("2026-08-22")
    puzzle = store.get_puzzle("ko", "2026-08-22")
    assert puzzle
    clue_id = puzzle["public"]["entries"][0]["id"]
    answer = puzzle["solution"]["answers"][clue_id]
    hint = service.create_hint_request(requester, puzzle["puzzle_id"], clue_id)
    raw = hint["shareUrl"].split("hint=", 1)[1]

    with pytest.raises(ValueError, match="DIRECT_HINT_NOT_ALLOWED"):
        service.answer_hint(raw, helper, f"정답은 {answer}")

    service.answer_hint(raw, helper, "정의 자체보다 문장에서 어떤 역할을 하는 개념인지 떠올려 보세요")
    visible = service.requester_hint(hint["requestId"], requester)
    assert visible["hint_text"] == "정의 자체보다 문장에서 어떤 역할을 하는 개념인지 떠올려 보세요"
    assert store.hint_for_requester(hint["requestId"], helper) is None
    assert len(store.leaderboard(requester, puzzle["puzzle_id"])) == 2
    assert service.verify_session(req_token) == requester
    assert service.verify_session(helper_token) == helper


def test_kakao_mention_and_quick_menu_do_not_require_typed_commands():
    assert _mention_to_command("@봇") == "봇 도움말"
    assert _mention_to_command("@봇 뉴스") == "봇 도움말"
    assert _mention_to_command("@봇 크로스워드") == "봇 크로스워드"
    payload = _attach_quick_menu({"version": "2.0", "template": {"outputs": []}})
    labels = [item["label"] for item in payload["template"]["quickReplies"]]
    assert labels == [item["label"] for item in KAKAO_QUICK_MENU]
    assert "📰 뉴스" in labels
    assert "🧩 크로스워드" in labels


def test_kakao_crossword_uses_buttons_without_printing_url(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSWORD_SQLITE_PATH", str(tmp_path / "crossword-menu.db"))
    monkeypatch.setenv("CROSSWORD_SIGNING_SECRET", "m" * 40)
    monkeypatch.setenv("CROSSWORD_PUBLIC_BASE_URL", "https://example.test")

    async def fake_payload(_request):
        return {}

    fake = SimpleNamespace(
        app=FastAPI(),
        _payload=fake_payload,
        _help=lambda: "기존 도움말",
        _strip_bot=lambda text: text[2:].strip() if text.startswith("봇 ") else ("도움말" if text == "봇" else text),
        answer=lambda message, _user: "기존 응답" if "도움말" not in message else "기존 도움말",
        _kakao=lambda text: {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": text}}]}},
    )
    install(fake)

    text = fake.answer("봇 크로스워드", "user-1")
    assert "https://" not in text
    assert "고등학교" in text
    kakao = fake._kakao(text)
    card = kakao["template"]["outputs"][0]["basicCard"]
    assert card["buttons"][0]["action"] == "webLink"
    assert card["buttons"][0]["webLinkUrl"].startswith("https://example.test/crossword?")
    assert "https://" not in card["description"]
    assert kakao["template"]["quickReplies"]


def test_browser_input_handles_hangul_ime_and_auto_advance():
    script = Path("src/telegram_news/crossword/static/app.js").read_text(encoding="utf-8")
    assert "compositionstart" in script
    assert "compositionend" in script
    assert "normalizeKoreanCell" in script
    assert ".normalize('NFKC').normalize('NFC')" in script
    assert "input.maxLength = 1" in script
    assert "removeAttribute('maxlength')" in script
    assert "moveInActive(input, 1)" in script
