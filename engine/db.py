"""SQLite 저장소 + 비밀번호 해시. 운영 시에는 Supabase(Postgres + Auth)로 교체할 것."""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from datetime import datetime
from pathlib import Path

PBKDF2_ROUNDS = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    salt TEXT NOT NULL,
    pw_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_date TEXT PRIMARY KEY,
    regime TEXT,
    regime_detail TEXT,
    data_source TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS recommendations (
    run_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    sector TEXT,
    opinion TEXT,
    score REAL,
    news_score REAL,
    buy_price INTEGER,
    sell_price INTEGER,
    stop_price INTEGER,
    prev_close INTEGER,
    reason TEXT,
    PRIMARY KEY (run_date, code)
);
CREATE TABLE IF NOT EXISTS user_entries (
    username TEXT NOT NULL,
    run_date TEXT NOT NULL,
    code TEXT NOT NULL,
    actual_price REAL,
    current_price REAL,
    updated_at TEXT,
    PRIMARY KEY (username, run_date, code)
);
"""


def connect(path: str | os.PathLike) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS).hex()


def create_user(conn: sqlite3.Connection, username: str, password: str) -> None:
    if not username or len(password) < 8:
        raise ValueError("ID는 필수이며 비밀번호는 8자 이상이어야 합니다.")
    salt = os.urandom(16)
    conn.execute(
        "INSERT OR REPLACE INTO users(username, salt, pw_hash) VALUES (?,?,?)",
        (username, salt.hex(), _hash(password, salt)),
    )
    conn.commit()


def verify_user(conn: sqlite3.Connection, username: str, password: str) -> bool:
    row = conn.execute("SELECT salt, pw_hash FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        # 존재 여부에 따른 응답 시간 차이를 줄이기 위해 더미 해시 수행
        _hash(password, b"\x00" * 16)
        return False
    return hmac.compare_digest(_hash(password, bytes.fromhex(row["salt"])), row["pw_hash"])


def user_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def save_run(conn: sqlite3.Connection, run_date: str, regime: dict, data_source: str, recs: list[dict]) -> None:
    import json

    conn.execute("DELETE FROM recommendations WHERE run_date=?", (run_date,))
    conn.execute(
        "INSERT OR REPLACE INTO runs(run_date, regime, regime_detail, data_source, created_at) VALUES (?,?,?,?,?)",
        (run_date, regime["mode"], json.dumps(regime, ensure_ascii=False), data_source, datetime.now().isoformat(timespec="seconds")),
    )
    conn.executemany(
        """INSERT INTO recommendations
           (run_date, code, name, sector, opinion, score, news_score, buy_price, sell_price, stop_price, prev_close, reason)
           VALUES (:run_date, :code, :name, :sector, :opinion, :score, :news_score, :buy_price, :sell_price, :stop_price, :prev_close, :reason)""",
        recs,
    )
    conn.commit()


def run_dates(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT run_date FROM runs ORDER BY run_date DESC")]


def get_run(conn: sqlite3.Connection, run_date: str):
    return conn.execute("SELECT * FROM runs WHERE run_date=?", (run_date,)).fetchone()


def get_recommendations(conn: sqlite3.Connection, run_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM recommendations WHERE run_date=? ORDER BY score DESC", (run_date,)
    ).fetchall()


def get_entries(conn: sqlite3.Connection, username: str, run_date: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM user_entries WHERE username=? AND run_date=?", (username, run_date)
    ).fetchall()
    return {r["code"]: r for r in rows}


def save_entry(conn: sqlite3.Connection, username: str, run_date: str, code: str, actual, current) -> None:
    """둘 다 비어 있으면 행을 삭제한다."""
    if actual is None and current is None:
        conn.execute(
            "DELETE FROM user_entries WHERE username=? AND run_date=? AND code=?",
            (username, run_date, code),
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO user_entries(username, run_date, code, actual_price, current_price, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (username, run_date, code, actual, current, datetime.now().isoformat(timespec="seconds")),
        )
    conn.commit()
