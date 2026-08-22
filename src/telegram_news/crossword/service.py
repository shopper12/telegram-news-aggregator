from __future__ import annotations

import base64, hashlib, hmac, json, os, secrets, time, uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import requests
from .generator import generate_puzzle, normalize_answer
from .storage import CrosswordStore

KST=ZoneInfo("Asia/Seoul")

class CrosswordService:
    def __init__(self,store:CrosswordStore|None=None):
        self.store=store or CrosswordStore(); self.secret=os.getenv("CROSSWORD_SIGNING_SECRET","dev-crossword-signing-secret-change-me"); self.public_base_url=os.getenv("CROSSWORD_PUBLIC_BASE_URL","http://localhost:8000").rstrip("/"); self.kakao_js_key=os.getenv("KAKAO_JAVASCRIPT_KEY","")
    @staticmethod
    def today_kst()->str:return datetime.now(KST).date().isoformat()
    def user_key(self,raw_user_id:str)->str:return hmac.new(self.secret.encode(),b"user:"+str(raw_user_id or "anonymous").encode(),hashlib.sha256).hexdigest()
    def default_nickname(self,user_key:str)->str:return f"친구-{user_key[:6]}"
    def issue_session(self,user_key:str,days:int=30)->str:
        payload={"u":user_key,"exp":int(time.time())+days*86400}; body=base64.urlsafe_b64encode(json.dumps(payload,separators=(",",":")).encode()).decode().rstrip("="); sig=base64.urlsafe_b64encode(hmac.new(self.secret.encode(),body.encode(),hashlib.sha256).digest()).decode().rstrip("="); return f"{body}.{sig}"
    def verify_session(self,token:str)->str:
        try:
            body,sig=token.split(".",1); expected=base64.urlsafe_b64encode(hmac.new(self.secret.encode(),body.encode(),hashlib.sha256).digest()).decode().rstrip("=")
            if not hmac.compare_digest(expected,sig):raise ValueError("bad signature")
            payload=json.loads(base64.urlsafe_b64decode(body+"="*(-len(body)%4)).decode())
            if int(payload["exp"])<int(time.time()):raise ValueError("expired")
            return str(payload["u"])
        except Exception as exc:raise ValueError("INVALID_SESSION") from exc
    def session_for_platform_user(self,raw_user_id:str)->tuple[str,str]:
        key=self.user_key(raw_user_id);self.store.ensure_user(key,self.default_nickname(key));return key,self.issue_session(key)
    def guest_session(self)->tuple[str,str]:return self.session_for_platform_user("guest:"+secrets.token_urlsafe(24))
    def game_url(self,session_token:str,language:str="ko",**params:str)->str:
        query={"s":session_token,"language":language,**{k:v for k,v in params.items() if v}};return f"{self.public_base_url}/crossword?{urlencode(query)}"
    def publish(self,publish_date:str|None=None)->list[dict]:
        day=publish_date or self.today_kst();results=[]
        for language in ("ko","en"):
            public,solution=generate_puzzle(language,day);puzzle_id=f"{day}:{language}";self.store.upsert_puzzle(puzzle_id,language,day,public,solution);results.append({"puzzle_id":puzzle_id,"language":language,"publish_date":day})
        return results
    def today(self,language:str)->dict:
        language="en" if language=="en" else "ko";day=self.today_kst();puzzle=self.store.get_puzzle(language,day)
        if puzzle is None:self.publish(day);puzzle=self.store.get_puzzle(language,day)
        assert puzzle is not None;return {"puzzle_id":puzzle["puzzle_id"],**puzzle["public"]}
    def start(self,user_key:str,puzzle_id:str)->dict:self.store.ensure_user(user_key,self.default_nickname(user_key));return self.store.start_play(user_key,puzzle_id)
    def submit(self,user_key:str,puzzle_id:str,answers:dict[str,str])->dict:
        play=self.store.get_play(user_key,puzzle_id)
        if play is None:raise ValueError("START_GAME_FIRST")
        if play.get("completed_at"):return {"correct":True,"score":play.get("score"),"elapsedMs":play.get("elapsed_ms"),"alreadyCompleted":True}
        language=puzzle_id.rsplit(":",1)[-1];puzzle=self.store.get_puzzle(language,puzzle_id[:10])
        if puzzle is None:raise ValueError("PUZZLE_NOT_FOUND")
        expected=puzzle["solution"]["answers"];wrong=[cid for cid,a in expected.items() if normalize_answer(answers.get(cid,""),language)!=normalize_answer(a,language)]
        if wrong:self.store.increment_wrong(user_key,puzzle_id);return {"correct":False,"wrongClueIds":wrong}
        start=datetime.fromisoformat(play["started_at"]);elapsed=max(1000,int((datetime.now(timezone.utc)-start).total_seconds()*1000));hints=self.store.count_answered_hints(user_key,puzzle_id);score=max(100,1000+max(0,600-elapsed//2000)-hints*150);self.store.complete_play(user_key,puzzle_id,elapsed,score);return {"correct":True,"score":score,"elapsedMs":elapsed,"hintsUsed":hints}
    def create_friend_invite(self,user_key:str)->str:
        raw=secrets.token_urlsafe(24);self.store.create_invite(hashlib.sha256(raw.encode()).hexdigest(),user_key,(datetime.now(timezone.utc)+timedelta(days=7)).isoformat());return f"{self.public_base_url}/crossword?invite={raw}"
    def accept_friend_invite(self,raw:str,user_key:str)->str:return self.store.accept_invite(hashlib.sha256(raw.encode()).hexdigest(),user_key)
    def create_hint_request(self,user_key:str,puzzle_id:str,clue_id:str)->dict:
        language=puzzle_id.rsplit(":",1)[-1];puzzle=self.store.get_puzzle(language,puzzle_id[:10])
        if not puzzle or clue_id not in {e["id"] for e in puzzle["public"]["entries"]}:raise ValueError("CLUE_NOT_FOUND")
        request_id=uuid.uuid4().hex;raw=secrets.token_urlsafe(24);self.store.create_hint(request_id,hashlib.sha256(raw.encode()).hexdigest(),user_key,puzzle_id,clue_id,(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat());return {"requestId":request_id,"shareUrl":f"{self.public_base_url}/crossword?hint={raw}"}
    def hint_help(self,raw:str)->dict:
        item=self.store.hint_by_token(hashlib.sha256(raw.encode()).hexdigest())
        if not item or item["expires_at"]<=datetime.now(timezone.utc).isoformat():raise ValueError("HINT_LINK_EXPIRED")
        clue=next((e for e in item["public"]["entries"] if e["id"]==item["clue_id"]),None)
        if not clue:raise ValueError("CLUE_NOT_FOUND")
        return {"status":item["status"],"clue":{k:clue[k] for k in ("number","clue","direction","length")}}
    def answer_hint(self,raw:str,helper_key:str,hint_text:str)->str:
        if len(hint_text.strip())<2 or len(hint_text.strip())>240:raise ValueError("INVALID_HINT")
        return self.store.answer_hint(hashlib.sha256(raw.encode()).hexdigest(),helper_key,hint_text.strip())
    def requester_hint(self,request_id:str,requester_key:str)->dict:
        item=self.store.hint_for_requester(request_id,requester_key)
        if not item:raise ValueError("HINT_NOT_FOUND")
        return item
    def broadcast_daily(self,publish_date:str|None=None)->dict:
        day=publish_date or self.today_kst();puzzles=self.publish(day);webhook=os.getenv("CROSSWORD_DAILY_BROADCAST_WEBHOOK","").strip();result={"published":puzzles,"broadcast":"skipped"}
        if webhook:
            payload={"event":"crossword.daily-published","publish_date":day,"title":"오늘의 한글 · 영어 크로스워드","message":"오늘의 새 크로스워드가 열렸습니다.","game_url":f"{self.public_base_url}/crossword"};headers={"content-type":"application/json"};token=os.getenv("CROSSWORD_DAILY_BROADCAST_TOKEN","").strip()
            if token:headers["authorization"]=f"Bearer {token}"
            response=requests.post(webhook,json=payload,headers=headers,timeout=10);response.raise_for_status();result["broadcast"]="sent"
        return result
