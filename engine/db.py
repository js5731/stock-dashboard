"""SQLite 저장소 + 비밀번호 해시. 운영 시에는 Supabase(Postgres + Auth)로 교체할 것."""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from datetime import datetime
from pathlib import Path

PBKDF2_ROUNDS = 200_000
ROLES = ("user", "admin")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    salt TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin'))
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
    removed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_date, code)
);
CREATE TABLE IF NOT EXISTS user_entries (
    username TEXT NOT NULL,
    run_date TEXT NOT NULL,
    code TEXT NOT NULL,
    actual_price REAL,
    current_price REAL,
    note TEXT,
    updated_at TEXT,
    PRIMARY KEY (username, run_date, code)
);
"""


def connect(path: str | os.PathLike) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if "role" not in {r["name"] for r in conn.execute("PRAGMA table_info(users)")}:  # 기존 DB 이전
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        conn.commit()
    for table, col, ddl in (
        ("recommendations", "removed", "INTEGER NOT NULL DEFAULT 0"),
        ("user_entries", "note", "TEXT"),
    ):  # 기존 DB 이전
        if col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            conn.commit()
    return conn


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS).hex()


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str | None = None) -> None:
    """계정 생성/비밀번호 갱신. role을 생략하면 신규는 'user', 기존 계정은 역할을 유지한다."""
    if not username or len(password) < 8:
        raise ValueError("ID는 필수이며 비밀번호는 8자 이상이어야 합니다.")
    if role is not None and role not in ROLES:
        raise ValueError(f"role은 {ROLES} 중 하나여야 합니다.")
    salt = os.urandom(16)
    conn.execute(
        """INSERT INTO users(username, salt, pw_hash, role) VALUES (?,?,?,COALESCE(?, 'user'))
           ON CONFLICT(username) DO UPDATE SET salt=excluded.salt, pw_hash=excluded.pw_hash,
           role=COALESCE(?, users.role)""",
        (username, salt.hex(), _hash(password, salt), role, role),
    )
    conn.commit()


def get_role(conn: sqlite3.Connection, username: str) -> str:
    row = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
    return row["role"] if row and row["role"] in ROLES else "user"


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

    conn.execute("DELETE FROM recommendations WHERE run_date=? AND removed=0", (run_date,))
    conn.execute(
        "INSERT OR REPLACE INTO runs(run_date, regime, regime_detail, data_source, created_at) VALUES (?,?,?,?,?)",
        (run_date, regime["mode"], json.dumps(regime, ensure_ascii=False), data_source, datetime.now().isoformat(timespec="seconds")),
    )
    conn.executemany(
        """INSERT OR REPLACE INTO recommendations
           (run_date, code, name, sector, opinion, score, news_score, buy_price, sell_price, stop_price, prev_close, reason)
           VALUES (:run_date, :code, :name, :sector, :opinion, :score, :news_score, :buy_price, :sell_price, :stop_price, :prev_close, :reason)""",
        recs,
    )
    conn.commit()


def run_dates(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT run_date FROM runs ORDER BY run_date DESC")]


def get_run(conn: sqlite3.Connection, run_date: str):
    return conn.execute("SELECT * FROM runs WHERE run_date=?", (run_date,)).fetchone()


def get_recommendations(conn: sqlite3.Connection, run_date: str, include_removed: bool = False) -> list[sqlite3.Row]:
    """include_removed=False(관리자 화면): 삭제 처리된 종목 제외. True: 전체."""
    where = "" if include_removed else " AND removed=0"
    return conn.execute(
        f"SELECT * FROM recommendations WHERE run_date=?{where} ORDER BY score DESC", (run_date,)
    ).fetchall()


def get_user_recommendations(conn: sqlite3.Connection, username: str, run_date: str) -> list[sqlite3.Row]:
    """사용자 화면: 관리자가 삭제했어도 해당 사용자가 실구매가를 입력한 종목은 계속 보인다."""
    return conn.execute(
        """SELECT r.* FROM recommendations r
           WHERE r.run_date=? AND (r.removed=0 OR EXISTS (
               SELECT 1 FROM user_entries e WHERE e.username=? AND e.run_date=r.run_date
               AND e.code=r.code AND e.actual_price IS NOT NULL))
           ORDER BY r.score DESC""",
        (run_date, username, ),
    ).fetchall()


def get_entries(conn: sqlite3.Connection, username: str, run_date: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM user_entries WHERE username=? AND run_date=?", (username, run_date)
    ).fetchall()
    return {r["code"]: r for r in rows}


def save_entry(conn: sqlite3.Connection, username: str, run_date: str, code: str, actual, current, note: str | None = None) -> None:
    """실구매가·현재가·비고가 모두 비어 있으면 행을 삭제한다."""
    note = (note or "").strip() or None
    if actual is None and current is None and note is None:
        conn.execute(
            "DELETE FROM user_entries WHERE username=? AND run_date=? AND code=?",
            (username, run_date, code),
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO user_entries(username, run_date, code, actual_price, current_price, note, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (username, run_date, code, actual, current, note, datetime.now().isoformat(timespec="seconds")),
        )
    conn.commit()


_REC_COLS = ("code", "name", "sector", "opinion", "score", "news_score",
             "buy_price", "sell_price", "stop_price", "prev_close", "reason")


def upsert_recommendation(conn: sqlite3.Connection, run_date: str, rec: dict) -> None:
    """수동 추가/수정. 같은 (run_date, code)가 있으면 덮어쓴다."""
    conn.execute(
        f"INSERT OR REPLACE INTO recommendations(run_date, {', '.join(_REC_COLS)}, removed) "
        f"VALUES (?{',?' * len(_REC_COLS)}, 0)",
        (run_date, *(rec.get(c) for c in _REC_COLS)),
    )
    conn.commit()


def delete_recommendation(conn: sqlite3.Connection, run_date: str, code: str) -> bool:
    """관리자 삭제. 실구매가를 입력한 사용자가 있으면 행을 남기고 removed=1로만 표시하고(그 사용자 화면에는 계속 보임),
    그렇지 않으면 완전히 삭제한다. 실구매가가 없는 사용자의 입력은 정리한다. 완전 삭제면 True."""
    held = conn.execute(
        "SELECT COUNT(*) FROM user_entries WHERE run_date=? AND code=? AND actual_price IS NOT NULL",
        (run_date, code),
    ).fetchone()[0]
    conn.execute("DELETE FROM user_entries WHERE run_date=? AND code=? AND actual_price IS NULL", (run_date, code))
    if held:
        conn.execute("UPDATE recommendations SET removed=1 WHERE run_date=? AND code=?", (run_date, code))
    else:
        conn.execute("DELETE FROM recommendations WHERE run_date=? AND code=?", (run_date, code))
    conn.commit()
    return not held


def code_history(conn: sqlite3.Connection, code: str) -> list[sqlite3.Row]:
    """한 종목의 날짜별 추천 이력(최신순)."""
    return conn.execute(
        "SELECT * FROM recommendations WHERE code=? AND removed=0 ORDER BY run_date DESC", (code,)
    ).fetchall()
