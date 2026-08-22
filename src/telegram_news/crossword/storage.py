from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrosswordStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url if database_url is not None else os.getenv("CROSSWORD_DATABASE_URL", "")
        self.is_postgres = self.database_url.startswith(("postgres://", "postgresql://"))
        self.sqlite_path = os.getenv("CROSSWORD_SQLITE_PATH", "/tmp/crossword.db")
        self._schema_ready = False

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.is_postgres:
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(self.database_url, row_factory=dict_row)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            path = Path(self.sqlite_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _sql(self, text: str) -> str:
        return text.replace("?", "%s") if self.is_postgres else text

    def _execute(self, conn: Any, sql: str, params: tuple = ()):
        return conn.execute(self._sql(sql), params)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        statements = [
            "CREATE TABLE IF NOT EXISTS crossword_users (user_key TEXT PRIMARY KEY, nickname TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS crossword_puzzles (puzzle_id TEXT PRIMARY KEY, language TEXT NOT NULL, publish_date TEXT NOT NULL, public_json TEXT NOT NULL, solution_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(language, publish_date))",
            "CREATE TABLE IF NOT EXISTS crossword_plays (user_key TEXT NOT NULL, puzzle_id TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, elapsed_ms INTEGER, score INTEGER, wrong_submissions INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_key, puzzle_id))",
            "CREATE TABLE IF NOT EXISTS crossword_friendships (user_low_key TEXT NOT NULL, user_high_key TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user_low_key, user_high_key))",
            "CREATE TABLE IF NOT EXISTS crossword_friend_invites (token_hash TEXT PRIMARY KEY, inviter_user_key TEXT NOT NULL, expires_at TEXT NOT NULL, accepted_by_user_key TEXT, accepted_at TEXT, created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS crossword_hint_requests (request_id TEXT PRIMARY KEY, requester_user_key TEXT NOT NULL, puzzle_id TEXT NOT NULL, clue_id TEXT NOT NULL, share_token_hash TEXT NOT NULL UNIQUE, status TEXT NOT NULL, hint_text TEXT, helper_user_key TEXT, expires_at TEXT NOT NULL, answered_at TEXT, created_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_crossword_puzzle_day ON crossword_puzzles(publish_date, language)",
            "CREATE INDEX IF NOT EXISTS idx_crossword_play_rank ON crossword_plays(puzzle_id, score DESC, elapsed_ms ASC)",
            "CREATE INDEX IF NOT EXISTS idx_crossword_hint_requester ON crossword_hint_requests(requester_user_key, puzzle_id)",
        ]
        with self.connect() as conn:
            for statement in statements:
                self._execute(conn, statement)
        self._schema_ready = True

    def ensure_user(self, user_key: str, nickname: str) -> dict:
        self.ensure_schema(); now = utc_now()
        with self.connect() as conn:
            row = self._execute(conn, "SELECT user_key,nickname FROM crossword_users WHERE user_key=?", (user_key,)).fetchone()
            if row is None:
                self._execute(conn, "INSERT INTO crossword_users(user_key,nickname,created_at,updated_at) VALUES(?,?,?,?)", (user_key, nickname, now, now))
                return {"user_key": user_key, "nickname": nickname}
            return dict(row)

    def set_nickname(self, user_key: str, nickname: str) -> None:
        self.ensure_schema()
        with self.connect() as conn:
            self._execute(conn, "UPDATE crossword_users SET nickname=?,updated_at=? WHERE user_key=?", (nickname, utc_now(), user_key))

    def upsert_puzzle(self, puzzle_id: str, language: str, publish_date: str, public: dict, solution: dict) -> None:
        self.ensure_schema(); now = utc_now()
        excluded = "EXCLUDED" if self.is_postgres else "excluded"
        sql = f"INSERT INTO crossword_puzzles(puzzle_id,language,publish_date,public_json,solution_json,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(language,publish_date) DO UPDATE SET public_json={excluded}.public_json,solution_json={excluded}.solution_json"
        with self.connect() as conn:
            self._execute(conn, sql, (puzzle_id, language, publish_date, json.dumps(public, ensure_ascii=False), json.dumps(solution, ensure_ascii=False), now))

    def get_puzzle(self, language: str, publish_date: str) -> dict | None:
        self.ensure_schema()
        with self.connect() as conn:
            row = self._execute(conn, "SELECT * FROM crossword_puzzles WHERE language=? AND publish_date=?", (language, publish_date)).fetchone()
        if row is None: return None
        item = dict(row); item["public"] = json.loads(item.pop("public_json")); item["solution"] = json.loads(item.pop("solution_json")); return item

    def start_play(self, user_key: str, puzzle_id: str) -> dict:
        self.ensure_schema()
        with self.connect() as conn:
            row = self._execute(conn, "SELECT * FROM crossword_plays WHERE user_key=? AND puzzle_id=?", (user_key,puzzle_id)).fetchone()
            if row is None:
                now=utc_now(); self._execute(conn, "INSERT INTO crossword_plays(user_key,puzzle_id,started_at) VALUES(?,?,?)", (user_key,puzzle_id,now)); return {"user_key":user_key,"puzzle_id":puzzle_id,"started_at":now,"completed_at":None}
            return dict(row)

    def get_play(self, user_key: str, puzzle_id: str) -> dict | None:
        self.ensure_schema()
        with self.connect() as conn: row=self._execute(conn,"SELECT * FROM crossword_plays WHERE user_key=? AND puzzle_id=?",(user_key,puzzle_id)).fetchone()
        return dict(row) if row else None

    def increment_wrong(self,user_key:str,puzzle_id:str)->None:
        with self.connect() as conn: self._execute(conn,"UPDATE crossword_plays SET wrong_submissions=wrong_submissions+1 WHERE user_key=? AND puzzle_id=?",(user_key,puzzle_id))

    def complete_play(self,user_key:str,puzzle_id:str,elapsed_ms:int,score:int)->None:
        with self.connect() as conn: self._execute(conn,"UPDATE crossword_plays SET completed_at=?,elapsed_ms=?,score=? WHERE user_key=? AND puzzle_id=?",(utc_now(),elapsed_ms,score,user_key,puzzle_id))

    def count_answered_hints(self,user_key:str,puzzle_id:str)->int:
        with self.connect() as conn: row=self._execute(conn,"SELECT COUNT(*) AS n FROM crossword_hint_requests WHERE requester_user_key=? AND puzzle_id=? AND status='answered'",(user_key,puzzle_id)).fetchone()
        return int(dict(row)["n"])

    def add_friendship(self,a:str,b:str)->None:
        if a==b:return
        low,high=sorted((a,b))
        with self.connect() as conn: self._execute(conn,"INSERT INTO crossword_friendships(user_low_key,user_high_key,created_at) VALUES(?,?,?) ON CONFLICT(user_low_key,user_high_key) DO NOTHING",(low,high,utc_now()))

    def create_invite(self,token_hash:str,user_key:str,expires_at:str)->None:
        with self.connect() as conn:self._execute(conn,"INSERT INTO crossword_friend_invites(token_hash,inviter_user_key,expires_at,created_at) VALUES(?,?,?,?)",(token_hash,user_key,expires_at,utc_now()))

    def accept_invite(self,token_hash:str,user_key:str)->str:
        now=utc_now()
        with self.connect() as conn:
            row=self._execute(conn,"SELECT * FROM crossword_friend_invites WHERE token_hash=?",(token_hash,)).fetchone()
            if row is None: raise ValueError("INVALID_INVITE")
            invite=dict(row)
            if invite["expires_at"]<=now: raise ValueError("INVITE_EXPIRED")
            if invite["inviter_user_key"]==user_key: raise ValueError("SELF_INVITE")
            if invite["accepted_by_user_key"] and invite["accepted_by_user_key"]!=user_key: raise ValueError("INVITE_USED")
            self._execute(conn,"UPDATE crossword_friend_invites SET accepted_by_user_key=?,accepted_at=? WHERE token_hash=?",(user_key,now,token_hash))
        self.add_friendship(invite["inviter_user_key"],user_key); return invite["inviter_user_key"]

    def leaderboard(self,user_key:str,puzzle_id:str)->list[dict]:
        self.ensure_schema()
        with self.connect() as conn:
            rows=self._execute(conn,"SELECT u.user_key,u.nickname,p.score,p.elapsed_ms FROM crossword_users u LEFT JOIN crossword_plays p ON p.user_key=u.user_key AND p.puzzle_id=? WHERE u.user_key=? OR EXISTS(SELECT 1 FROM crossword_friendships f WHERE (f.user_low_key=? AND f.user_high_key=u.user_key) OR (f.user_high_key=? AND f.user_low_key=u.user_key))",(puzzle_id,user_key,user_key,user_key)).fetchall()
        players=[dict(r) for r in rows]; players.sort(key=lambda r:(r["score"] is None,-(r["score"] or 0),r["elapsed_ms"] or 10**18,r["nickname"])); rank=0
        for item in players:
            item["is_me"]=item["user_key"]==user_key
            if item["score"] is not None: rank+=1; item["rank"]=rank
            else:item["rank"]=None
            item.pop("user_key",None)
        return players

    def create_hint(self,request_id:str,token_hash:str,requester:str,puzzle_id:str,clue_id:str,expires_at:str)->None:
        with self.connect() as conn:self._execute(conn,"INSERT INTO crossword_hint_requests(request_id,requester_user_key,puzzle_id,clue_id,share_token_hash,status,expires_at,created_at) VALUES(?,?,?,?,?,'pending',?,?)",(request_id,requester,puzzle_id,clue_id,token_hash,expires_at,utc_now()))

    def hint_by_token(self,token_hash:str)->dict|None:
        with self.connect() as conn: row=self._execute(conn,"SELECT h.*,p.public_json FROM crossword_hint_requests h JOIN crossword_puzzles p ON p.puzzle_id=h.puzzle_id WHERE h.share_token_hash=?",(token_hash,)).fetchone()
        if row is None:return None
        result=dict(row);result["public"]=json.loads(result.pop("public_json"));return result

    def answer_hint(self,token_hash:str,helper:str,hint_text:str)->str:
        now=utc_now()
        with self.connect() as conn:
            row=self._execute(conn,"SELECT * FROM crossword_hint_requests WHERE share_token_hash=?",(token_hash,)).fetchone()
            if row is None:raise ValueError("HINT_NOT_FOUND")
            item=dict(row)
            if item["expires_at"]<=now or item["status"]!="pending":raise ValueError("HINT_NOT_AVAILABLE")
            if item["requester_user_key"]==helper:raise ValueError("SELF_HINT")
            self._execute(conn,"UPDATE crossword_hint_requests SET helper_user_key=?,hint_text=?,status='answered',answered_at=? WHERE share_token_hash=?",(helper,hint_text,now,token_hash))
        self.add_friendship(item["requester_user_key"],helper);return item["request_id"]

    def hint_for_requester(self,request_id:str,requester:str)->dict|None:
        with self.connect() as conn:row=self._execute(conn,"SELECT request_id,status,clue_id,hint_text,answered_at FROM crossword_hint_requests WHERE request_id=? AND requester_user_key=?",(request_id,requester)).fetchone()
        return dict(row) if row else None
