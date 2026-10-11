"""Streamlit AppTest: 로그인 → 추천 표 → 수익률(공란/계산) → 저장 / 현재가 자동 채우기."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from engine import db
from engine.data import DATA_DIR
from jobs.daily_job import run

APP = str(Path(__file__).resolve().parent.parent / "app.py")
RUN_DATE = "2026-10-09"
EXPECTED_COLS = ["종목", "의견", "매수가", "매도가", "손절가", "실구매가", "현재가", "수익률(%)", "선정 사유"]


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = str(tmp_path / "app.db")
    monkeypatch.setenv("STOCK_DB", path)
    conn = db.connect(path)
    db.create_user(conn, "tester", "test-pass-123")
    conn.close()
    run(path, (DATA_DIR / "sample_news.txt").read_text(encoding="utf-8"), "sample", "공격", date(2026, 10, 9))
    return path


def _login(at, user, pw):
    at.text_input[0].set_value(user)
    at.text_input[1].set_value(pw)
    at.button[0].click()
    return at.run()


def _logged_in(user="tester", pw="test-pass-123"):
    at = AppTest.from_file(APP, default_timeout=30).run()
    return _login(at, user, pw)


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _table(at) -> pd.DataFrame:
    return at.dataframe[0].value


def _codes(path):
    conn = db.connect(path)
    try:
        return [r["code"] for r in db.get_recommendations(conn, RUN_DATE)]
    finally:
        conn.close()


def test_login_failure_shows_error(app_db):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at = _login(at, "tester", "wrong")
    assert at.error and "올바르지 않습니다" in at.error[0].value
    assert "user" not in at.session_state


def test_no_users_shows_setup_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_DB", str(tmp_path / "empty.db"))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert at.warning and "create_user" in at.warning[0].value


def test_login_success_shows_table_with_blank_profit(app_db):
    at = _logged_in()
    assert not at.exception
    assert at.session_state["user"] == "tester"
    assert any("샘플" in e.value for e in at.error)  # 가짜 데이터 경고
    df = _table(at)
    assert list(df.columns) == EXPECTED_COLS
    assert len(df) == len(_codes(app_db)) == 5
    assert df["수익률(%)"].isna().all()  # 입력 전에는 공란
    # 매도/회피 행: 매수가·손절가 공란, 매도가는 있음
    sell_rows = df[df["의견"] == "매도/회피"]
    assert len(sell_rows) > 0
    assert sell_rows["매수가"].isna().all() and sell_rows["매도가"].notna().all()


def test_profit_rate_shown_only_when_both_prices_exist(app_db):
    codes = _codes(app_db)
    conn = db.connect(app_db)
    db.save_entry(conn, "tester", RUN_DATE, codes[0], 100_000.0, 110_000.0)  # 둘 다 → +10%
    db.save_entry(conn, "tester", RUN_DATE, codes[1], 100_000.0, None)       # 실구매가만 → 공란
    conn.close()
    df = _table(_logged_in())
    assert df.loc[0, "수익률(%)"] == pytest.approx(10.0)
    assert pd.isna(df.loc[1, "수익률(%)"])
    assert df.loc[1, "실구매가"] == 100_000 and pd.isna(df.loc[1, "현재가"])


def test_entries_are_not_visible_to_other_users(app_db):
    codes = _codes(app_db)
    conn = db.connect(app_db)
    db.create_user(conn, "other", "other-pass-123")
    db.save_entry(conn, "tester", RUN_DATE, codes[0], 100_000.0, 110_000.0)
    conn.close()
    df = _table(_logged_in("other", "other-pass-123"))
    assert df["실구매가"].isna().all() and df["수익률(%)"].isna().all()


def test_save_button_keeps_existing_entries(app_db):
    codes = _codes(app_db)
    conn = db.connect(app_db)
    db.save_entry(conn, "tester", RUN_DATE, codes[0], 100_000.0, 110_000.0)
    conn.close()
    at = _logged_in()
    _button(at, "저장").click()
    at.run()
    assert not at.exception
    conn = db.connect(app_db)
    entries = db.get_entries(conn, "tester", RUN_DATE)
    conn.close()
    assert set(entries) == {codes[0]}  # 입력 없는 행이 새로 생기지 않음
    assert entries[codes[0]]["actual_price"] == 100_000.0


def test_auto_fill_current_price_keeps_actual(app_db):
    codes = _codes(app_db)
    conn = db.connect(app_db)
    db.save_entry(conn, "tester", RUN_DATE, codes[0], 100_000.0, None)
    conn.close()
    at = _logged_in()
    _button(at, "현재가 자동 채우기").click()
    at.run()
    assert not at.exception
    conn = db.connect(app_db)
    entries = db.get_entries(conn, "tester", RUN_DATE)
    conn.close()
    assert set(entries) == set(codes)
    assert all(e["current_price"] and e["current_price"] > 0 for e in entries.values())
    assert entries[codes[0]]["actual_price"] == 100_000.0
    assert _table(at).loc[0, "수익률(%)"] is not None  # 실구매가가 있는 행은 수익률 계산됨


def test_logout(app_db):
    at = _logged_in()
    _button(at, "로그아웃").click()
    at.run()
    assert "user" not in at.session_state
    assert at.text_input  # 로그인 화면으로 복귀


def test_lockout_after_five_failures(app_db):
    at = AppTest.from_file(APP, default_timeout=30).run()
    for _ in range(5):
        at = _login(at, "tester", "bad")
    at = _login(at, "tester", "test-pass-123")  # 올바른 비번이라도 차단
    assert "user" not in at.session_state
    assert any("초과" in e.value for e in at.error)
