"""PostgreSQL (Supabase) 저장소. SUPABASE_DB_URL 환경변수 설정 시 db.py 대신 사용."""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime

import psycopg2
import psycopg2.extras

PBKDF2_ROUNDS = 200_000
ROLES = ("user", "admin")

_SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        salt TEXT NOT NULL,
        pw_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin'))
    )""",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'",
    """CREATE TABLE IF NOT EXISTS runs (
        run_date TEXT PRIMARY KEY,
        regime TEXT,
        regime_detail TEXT,
        data_source TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS recommendations (
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
    )""",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS removed INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS user_entries (
        username TEXT NOT NULL,
        run_date TEXT NOT NULL,
        code TEXT NOT NULL,
        actual_price REAL,
        current_price REAL,
        updated_at TEXT,
        PRIMARY KEY (username, run_date, code)
    )""",
    "ALTER TABLE user_entries ADD COLUMN IF NOT EXISTS note TEXT",
]


def connect(url: str):
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        for stmt in _SCHEMA_STMTS:
            cur.execute(stmt)
    conn.commit()
    return conn


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS).hex()


def create_user(conn, username: str, password: str, role: str | None = None) -> None:
    """계정 생성/비밀번호 갱신. role을 생략하면 신규는 'user', 기존 계정은 역할을 유지한다."""
    if not username or len(password) < 8:
        raise ValueError("ID는 필수이며 비밀번호는 8자 이상이어야 합니다.")
    if role is not None and role not in ROLES:
        raise ValueError(f"role은 {ROLES} 중 하나여야 합니다.")
    salt = os.urandom(16)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO users(username, salt, pw_hash, role) VALUES (%s, %s, %s, COALESCE(%s, 'user'))
               ON CONFLICT (username) DO UPDATE SET salt = EXCLUDED.salt, pw_hash = EXCLUDED.pw_hash,
               role = COALESCE(%s, users.role)""",
            (username, salt.hex(), _hash(password, salt), role, role),
        )
    conn.commit()


def get_role(conn, username: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT role FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
    return row["role"] if row and row["role"] in ROLES else "user"


def verify_user(conn, username: str, password: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT salt, pw_hash FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
    if row is None:
        _hash(password, b"\x00" * 16)
        return False
    return hmac.compare_digest(_hash(password, bytes.fromhex(row["salt"])), row["pw_hash"])


def user_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        return cur.fetchone()["cnt"]


def save_run(conn, run_date: str, regime: dict, data_source: str, recs: list[dict]) -> None:
    import json
    with conn.cursor() as cur:
        cur.execute("DELETE FROM recommendations WHERE run_date = %s AND removed = 0", (run_date,))
        cur.execute(
            """INSERT INTO runs(run_date, regime, regime_detail, data_source, created_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (run_date) DO UPDATE SET
                 regime = EXCLUDED.regime,
                 regime_detail = EXCLUDED.regime_detail,
                 data_source = EXCLUDED.data_source,
                 created_at = EXCLUDED.created_at""",
            (run_date, regime["mode"], json.dumps(regime, ensure_ascii=False),
             data_source, datetime.now().isoformat(timespec="seconds")),
        )
        if recs:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO recommendations
                   (run_date, code, name, sector, opinion, score, news_score,
                    buy_price, sell_price, stop_price, prev_close, reason)
                   VALUES %s
                   ON CONFLICT (run_date, code) DO UPDATE SET
                     name = EXCLUDED.name, sector = EXCLUDED.sector, opinion = EXCLUDED.opinion,
                     score = EXCLUDED.score, news_score = EXCLUDED.news_score,
                     buy_price = EXCLUDED.buy_price, sell_price = EXCLUDED.sell_price,
                     stop_price = EXCLUDED.stop_price, prev_close = EXCLUDED.prev_close,
                     reason = EXCLUDED.reason, removed = 0""",
                [(r["run_date"], r["code"], r["name"], r["sector"], r["opinion"],
                  r["score"], r["news_score"], r["buy_price"], r["sell_price"],
                  r["stop_price"], r["prev_close"], r["reason"]) for r in recs],
            )
    conn.commit()


def run_dates(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT run_date FROM runs ORDER BY run_date DESC")
        return [r["run_date"] for r in cur.fetchall()]


def get_run(conn, run_date: str):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM runs WHERE run_date = %s", (run_date,))
        return cur.fetchone()


def get_recommendations(conn, run_date: str, include_removed: bool = False) -> list:
    where = "" if include_removed else " AND removed = 0"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM recommendations WHERE run_date = %s{where} ORDER BY score DESC", (run_date,)
        )
        return cur.fetchall()


def get_user_recommendations(conn, username: str, run_date: str) -> list:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.* FROM recommendations r
               WHERE r.run_date = %s AND (r.removed = 0 OR EXISTS (
                   SELECT 1 FROM user_entries e WHERE e.username = %s AND e.run_date = r.run_date
                   AND e.code = r.code AND e.actual_price IS NOT NULL))
               ORDER BY r.score DESC""",
            (run_date, username),
        )
        return cur.fetchall()


def get_entries(conn, username: str, run_date: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM user_entries WHERE username = %s AND run_date = %s", (username, run_date)
        )
        return {r["code"]: r for r in cur.fetchall()}


def save_entry(conn, username: str, run_date: str, code: str, actual, current, note: str | None = None) -> None:
    note = (note or "").strip() or None
    with conn.cursor() as cur:
        if actual is None and current is None and note is None:
            cur.execute(
                "DELETE FROM user_entries WHERE username = %s AND run_date = %s AND code = %s",
                (username, run_date, code),
            )
        else:
            cur.execute(
                """INSERT INTO user_entries(username, run_date, code, actual_price, current_price, note, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (username, run_date, code) DO UPDATE SET
                     actual_price = EXCLUDED.actual_price,
                     current_price = EXCLUDED.current_price,
                     note = EXCLUDED.note,
                     updated_at = EXCLUDED.updated_at""",
                (username, run_date, code, actual, current, note,
                 datetime.now().isoformat(timespec="seconds")),
            )
    conn.commit()


_REC_COLS = ("code", "name", "sector", "opinion", "score", "news_score",
             "buy_price", "sell_price", "stop_price", "prev_close", "reason")


def upsert_recommendation(conn, run_date: str, rec: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO recommendations(run_date, {', '.join(_REC_COLS)})
                VALUES (%s{', %s' * len(_REC_COLS)})
                ON CONFLICT (run_date, code) DO UPDATE SET
                {', '.join(f'{c} = EXCLUDED.{c}' for c in _REC_COLS if c != 'code')}, removed = 0""",
            (run_date, *(rec.get(c) for c in _REC_COLS)),
        )
    conn.commit()


def delete_recommendation(conn, run_date: str, code: str) -> bool:
    """db.delete_recommendation 과 동일 규칙. 완전 삭제면 True."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM user_entries WHERE run_date = %s AND code = %s AND actual_price IS NOT NULL",
            (run_date, code),
        )
        held = cur.fetchone()["cnt"]
        cur.execute("DELETE FROM user_entries WHERE run_date = %s AND code = %s AND actual_price IS NULL", (run_date, code))
        if held:
            cur.execute("UPDATE recommendations SET removed = 1 WHERE run_date = %s AND code = %s", (run_date, code))
        else:
            cur.execute("DELETE FROM recommendations WHERE run_date = %s AND code = %s", (run_date, code))
    conn.commit()
    return not held


def code_history(conn, code: str) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM recommendations WHERE code = %s AND removed = 0 ORDER BY run_date DESC", (code,))
        return cur.fetchall()
