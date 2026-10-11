import pytest

from engine import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_password_is_hashed_and_verified(conn):
    db.create_user(conn, "alice", "correct-horse")
    stored = conn.execute("SELECT * FROM users WHERE username='alice'").fetchone()
    assert "correct-horse" not in (stored["salt"] + stored["pw_hash"])
    assert db.verify_user(conn, "alice", "correct-horse")
    assert not db.verify_user(conn, "alice", "wrong-password")
    assert not db.verify_user(conn, "nobody", "correct-horse")


def test_short_password_rejected(conn):
    with pytest.raises(ValueError):
        db.create_user(conn, "bob", "short")


def test_same_password_gets_different_hash(conn):
    db.create_user(conn, "a", "same-password")
    db.create_user(conn, "b", "same-password")
    rows = conn.execute("SELECT pw_hash FROM users").fetchall()
    assert rows[0]["pw_hash"] != rows[1]["pw_hash"]


def _rec(code):
    return {"run_date": "2026-10-09", "code": code, "name": "N", "sector": "S", "opinion": "매수", "score": 50.0,
            "news_score": 40.0, "buy_price": 100, "sell_price": 110, "stop_price": 95, "prev_close": 101, "reason": "r"}


def test_run_roundtrip_and_overwrite(conn):
    regime = {"mode": "공격", "last": 1, "ma": 1, "gap_pct": 0}
    db.save_run(conn, "2026-10-09", regime, "sample", [_rec("A"), _rec("B")])
    db.save_run(conn, "2026-10-09", regime, "sample", [_rec("C")])  # 같은 날 재실행 시 교체
    assert [r["code"] for r in db.get_recommendations(conn, "2026-10-09")] == ["C"]
    assert db.run_dates(conn) == ["2026-10-09"]


def test_entries_are_per_user_and_blank_deletes(conn):
    db.save_entry(conn, "u1", "2026-10-09", "A", 100.0, 110.0)
    db.save_entry(conn, "u2", "2026-10-09", "A", 200.0, None)
    assert db.get_entries(conn, "u1", "2026-10-09")["A"]["actual_price"] == 100.0
    assert db.get_entries(conn, "u2", "2026-10-09")["A"]["current_price"] is None
    db.save_entry(conn, "u1", "2026-10-09", "A", None, None)
    assert db.get_entries(conn, "u1", "2026-10-09") == {}
    assert "A" in db.get_entries(conn, "u2", "2026-10-09")  # 다른 사용자 데이터는 그대로


def test_role_defaults_to_user_and_admin_survives_password_reset(conn):
    db.create_user(conn, "kim", "password-1")
    assert db.get_role(conn, "kim") == "user"
    db.create_user(conn, "kim", "password-2", "admin")
    assert db.get_role(conn, "kim") == "admin"
    db.create_user(conn, "kim", "password-3")  # role 생략 → 관리자 유지
    assert db.get_role(conn, "kim") == "admin"
    assert db.verify_user(conn, "kim", "password-3")


def test_invalid_role_rejected_and_unknown_user_is_user(conn):
    with pytest.raises(ValueError):
        db.create_user(conn, "x", "password-1", "root")
    assert db.get_role(conn, "nobody") == "user"


def test_existing_db_without_role_column_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE users (username TEXT PRIMARY KEY, salt TEXT NOT NULL, pw_hash TEXT NOT NULL)")
    old.commit(); old.close()
    c = db.connect(path)
    db.create_user(c, "a", "password-1")
    assert db.get_role(c, "a") == "user"
    c.close()
