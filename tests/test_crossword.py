from datetime import date,timedelta
from telegram_news.crossword.generator import generate_puzzle,normalize_answer
from telegram_news.crossword.service import CrosswordService
from telegram_news.crossword.storage import CrosswordStore

def test_generator_one_year_two_languages():
    start=date(2026,1,1)
    for offset in range(365):
        day=(start+timedelta(days=offset)).isoformat()
        for lang in ("ko","en"):
            public,solution=generate_puzzle(lang,day);assert len(public["entries"])>=5;assert len(solution["answers"])==len(public["entries"]);serialized=str(public)
            for answer in solution["answers"].values():assert answer not in serialized

def test_normalize_answers():
    assert normalize_answer(" apple ","en")=="APPLE";assert normalize_answer(" 사 과 ","ko")=="사과"

def test_requester_only_hint_and_friendship(tmp_path,monkeypatch):
    monkeypatch.setenv("CROSSWORD_SQLITE_PATH",str(tmp_path/"crossword.db"));monkeypatch.setenv("CROSSWORD_SIGNING_SECRET","x"*40);monkeypatch.setenv("CROSSWORD_PUBLIC_BASE_URL","https://example.test")
    store=CrosswordStore("");service=CrosswordService(store);requester,req_token=service.session_for_platform_user("kakao:requester");helper,helper_token=service.session_for_platform_user("kakao:helper");service.publish("2026-08-22");puzzle=store.get_puzzle("ko","2026-08-22");assert puzzle;clue_id=puzzle["public"]["entries"][0]["id"];hint=service.create_hint_request(requester,puzzle["puzzle_id"],clue_id);raw=hint["shareUrl"].split("hint=",1)[1];service.answer_hint(raw,helper,"첫 글자를 떠올려 보세요");visible=service.requester_hint(hint["requestId"],requester);assert visible["hint_text"]=="첫 글자를 떠올려 보세요";assert store.hint_for_requester(hint["requestId"],helper) is None;assert len(store.leaderboard(requester,puzzle["puzzle_id"]))==2;assert service.verify_session(req_token)==requester;assert service.verify_session(helper_token)==helper
