from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class NewsMessage:
    channel_name: str
    channel_username: str
    category: str
    message_id: int
    message_date: datetime
    text: str
    normalized_text: str


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name TEXT NOT NULL,
            channel_username TEXT NOT NULL,
            category TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            message_date TEXT NOT NULL,
            text TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(channel_username, message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_messages_date
        ON messages(message_date);

        CREATE INDEX IF NOT EXISTS idx_messages_norm
        ON messages(normalized_text);
        """
    )
    conn.commit()


def insert_messages(conn: sqlite3.Connection, messages: list[NewsMessage]) -> int:
    inserted = 0
    now = datetime.now(timezone.utc).isoformat()

    for m in messages:
        try:
            conn.execute(
                """
                INSERT INTO messages
                (channel_name, channel_username, category, message_id, message_date, text, normalized_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m.channel_name,
                    m.channel_username,
                    m.category,
                    m.message_id,
                    m.message_date.isoformat(),
                    m.text,
                    m.normalized_text,
                    now,
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    return inserted


def fetch_recent(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    cur = conn.execute(
        """
        SELECT *
        FROM messages
        WHERE message_date >= ?
        ORDER BY message_date DESC
        """,
        (since.isoformat(),),
    )
    return list(cur.fetchall())
