"""Streamlit AppTest: 로그인 → 추천 표 → 수익률(공란/계산) → 저장 / 현재가 자동 채우기."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from engine import db
from engine.data import DATA_DIR, load_universe
from jobs.daily_job import run

APP = str(Path(__file__).resolve().parent.parent / "app.py")
RUN_DATE = "2026-10-09"
EXPECTED_COLS = ["구분", "종목", "의견", "매수가", "매도가", "손절가", "실구매가", "현재가", "수익률(%)", "선정 사유", "비고"]


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = str(tmp_path / "app.db")
    monkeypatch.setenv("STOCK_DB", path)
    conn = db.connect(path)
    db.create_user(conn, "tester", "test-pass-123")
    conn.close()
    run(path, (DATA_DIR / "sample_news.txt").read_text(encoding="utf-8"), "sample", "공격", date(2026, 10, 9))
    return path


class _OfflineKiwoom:
    """Object 화면 테스트가 .env의 실제 키로 외부 API를 호출하지 않게 막는다."""

    info: dict = {}

    def stock_info(self, code):
        return self.info

    def latest_price(self, code):
        return None

    def prev_close(self, code):
        return None


@pytest.fixture(autouse=True)
def _no_real_kiwoom_in_object_tests(request, monkeypatch):
    if request.node.name.startswith("test_object"):
        monkeypatch.setattr("engine.data.KiwoomQuote", lambda: _OfflineKiwoom())


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


def test_query_button_without_keys_shows_error(app_db, monkeypatch):
    monkeypatch.delenv("KIWOOM_APPKEY", raising=False)
    monkeypatch.delenv("KIWOOM_SECRETKEY", raising=False)
    at = _logged_in()
    _button(at, "조회").click()
    at.run()
    assert not at.exception
    assert any("KIWOOM" in e.value for e in at.error)


def test_query_fills_current_price_keeps_actual(app_db, monkeypatch):
    class FakeKiwoom:
        def latest_price(self, code):
            return 12_345

    monkeypatch.setattr("engine.data.KiwoomQuote", lambda: FakeKiwoom())
    codes = _codes(app_db)
    conn = db.connect(app_db)
    db.save_entry(conn, "tester", RUN_DATE, codes[0], 100_000.0, None)
    conn.close()
    at = _logged_in()
    _button(at, "조회").click()
    at.run()
    assert not at.exception
    conn = db.connect(app_db)
    entries = db.get_entries(conn, "tester", RUN_DATE)
    conn.close()
    assert set(entries) == set(codes)
    assert all(e["current_price"] == 12_345 for e in entries.values())
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


def test_admin_role_is_loaded_at_login(app_db):
    conn = db.connect(app_db)
    db.create_user(conn, "boss", "boss-pass-123", "admin")
    conn.close()
    at = _logged_in("boss", "boss-pass-123")
    assert at.session_state["role"] == "admin"
    assert any("관리자" in m.value for m in at.sidebar.markdown)
    _button(at, "로그아웃").click()
    at.run()
    assert "role" not in at.session_state


def test_normal_user_role_is_user(app_db):
    at = _logged_in()
    assert at.session_state["role"] == "user"
    assert not any("관리자" in m.value for m in at.sidebar.markdown)


# ───────── 메뉴 / 역할 ─────────
def _admin(app_db):
    conn = db.connect(app_db)
    db.create_user(conn, "boss", "boss-pass-123", "admin")
    conn.close()
    return _logged_in("boss", "boss-pass-123")


def test_user_sees_only_overview_menu(app_db):
    at = _logged_in()
    assert list(at.sidebar.radio[0].options) == ["Overview"]


def test_admin_sees_all_menus_and_overview_default(app_db):
    at = _admin(app_db)
    assert list(at.sidebar.radio[0].options) == ["Overview", "Analysis", "Object"]
    assert list(_table(at).columns) == EXPECTED_COLS


def test_analysis_lookup(app_db):
    at = _admin(app_db)
    at.sidebar.radio[0].set_value("Analysis").run()
    assert not at.exception
    at.text_input[0].set_value("삼성전자").run()
    assert not at.exception
    assert any("삼성전자" in s.value for s in at.subheader)
    assert any("샘플" in e.value for e in at.error)  # 가짜 시세 경고 유지
    at.text_input[0].set_value("없는종목xyz").run()
    assert at.warning


def test_delete_without_holders_removes_row(app_db):
    codes = _codes(app_db)
    conn = db.connect(app_db)
    assert db.delete_recommendation(conn, RUN_DATE, codes[0]) is True
    assert len(db.get_recommendations(conn, RUN_DATE, include_removed=True)) == 4
    conn.close()


def test_removed_stock_hidden_from_object_page(app_db):
    codes = _codes(app_db)
    conn = db.connect(app_db)
    db.save_entry(conn, "tester", RUN_DATE, codes[0], 100_000.0, None)
    db.delete_recommendation(conn, RUN_DATE, codes[0])
    db.create_user(conn, "boss", "boss-pass-123", "admin")
    conn.close()
    at = _logged_in("boss", "boss-pass-123")
    at.sidebar.radio[0].set_value("Object").run()
    assert len(at.dataframe[0].value) == 4


def test_note_is_saved_per_user(app_db):
    codes = _codes(app_db)
    conn = db.connect(app_db)
    db.save_entry(conn, "tester", RUN_DATE, codes[0], None, None, "  메모  ")
    db.create_user(conn, "other", "other-pass-123")
    entries = db.get_entries(conn, "tester", RUN_DATE)
    assert entries[codes[0]]["note"] == "메모"  # 비고만 있어도 행 유지
    assert db.get_entries(conn, "other", RUN_DATE) == {}
    conn.close()
    df = _table(_logged_in())
    assert df.loc[0, "비고"] == "메모" and df["수익률(%)"].isna().all()


def test_save_hint_and_confirmation_message(app_db):
    at = _logged_in()
    assert any("[저장]" in i.value for i in at.info)  # 저장 버튼을 눌러야 저장된다는 안내
    _button(at, "저장").click()
    at.run()
    assert not at.exception
    assert any("저장되었습니다" in s.value for s in at.success)


def test_admin_sees_stock_shortcuts_but_user_does_not(app_db):
    assert len(_admin(app_db).dataframe) == 2          # 추천 표 + 종목 바로가기
    assert len(_logged_in().dataframe) == 1            # 일반 사용자는 Analysis 권한이 없어 바로가기 없음


def test_analysis_opens_with_preset_stock(app_db):
    at = _admin(app_db)
    at.session_state["menu"] = "Analysis"
    at.session_state["analysis_query"] = "005930"
    at.run()
    assert not at.exception
    assert any("삼성전자" in s.value for s in at.subheader)


# ───────── Object: 추가 / 수정 / 삭제 ─────────
def _object_page(app_db):
    at = _admin(app_db)
    at.sidebar.radio[0].set_value("Object").run()
    assert not at.exception
    return at


def _fill_new(at, code="999999", name="테스트종목", prev=50_000, buy=49_550, sell=51_800, stop=48_050):
    """코드 입력 → 재실행(자동 채움 발생) → 나머지 칸을 덮어쓴다."""
    {t.label: t for t in at.text_input}["종목코드 (6자리)"].set_value(code)
    at.run()
    {t.label: t for t in at.text_input}["종목명"].set_value(name)
    n = {x.label: x for x in at.number_input}
    n["전일종가"].set_value(prev)
    n["매수가"].set_value(buy)
    n["매도가(목표가)"].set_value(sell)
    n["손절가"].set_value(stop)


def _submit(at):
    next(b for b in at.button if b.label == "저장").click()
    return at.run()


def test_object_edit_and_delete_disabled_without_selection(app_db):
    at = _object_page(app_db)
    assert _button(at, "수정").disabled and _button(at, "삭제").disabled
    assert not _button(at, "종목 추가").disabled


def test_object_add_form_and_save(app_db):
    at = _object_page(app_db)
    assert not at.text_input  # 추가 버튼 전에는 폼이 없다
    _button(at, "종목 추가").click()
    at.run()
    _fill_new(at)
    at = _submit(at)
    assert not at.exception and not at.error
    assert "999999" in _codes(app_db)


def test_object_rejects_invalid_prices(app_db):
    at = _object_page(app_db)
    _button(at, "종목 추가").click()
    at.run()
    _fill_new(at, buy=51_000, sell=50_500)  # 매수가 > 목표가
    at = _submit(at)
    assert at.error
    assert "999999" not in _codes(app_db)


def test_object_add_rejects_duplicate_code(app_db):
    existing = _codes(app_db)[0]
    at = _object_page(app_db)
    _button(at, "종목 추가").click()
    at.run()
    _fill_new(at, code=existing)
    at = _submit(at)
    assert any("이미 등록" in e.value for e in at.error)


def test_upsert_overwrites_existing_row(app_db):
    # AppTest는 표 행 선택을 흉내 낼 수 없어 수정 저장 경로(upsert)를 DB 수준에서 확인한다.
    code = _codes(app_db)[0]
    conn = db.connect(app_db)
    rec = {k: v for k, v in dict(db.get_recommendations(conn, RUN_DATE)[0]).items() if k not in ("run_date", "removed")}
    rec["reason"] = "수정됨"
    db.upsert_recommendation(conn, RUN_DATE, rec)
    after = [r for r in db.get_recommendations(conn, RUN_DATE) if r["code"] == code]
    conn.close()
    assert len(after) == 1 and after[0]["reason"] == "수정됨"


def test_object_code_autofills_from_universe_and_sample_price(app_db):
    uni = load_universe()
    row = uni[~uni["code"].isin(_codes(app_db))].iloc[0]  # 아직 추천에 없는 종목
    at = _object_page(app_db)
    _button(at, "종목 추가").click()
    at.run()
    {t.label: t for t in at.text_input}["종목코드 (6자리)"].set_value(row["code"])
    at.run()
    assert not at.exception
    f = {t.label: t for t in at.text_input}
    n = {x.label: x for x in at.number_input}
    assert f["종목명"].value == row["name"] and f["섹터"].value == row["sector"]
    assert n["전일종가"].value == row["sample_price"]
    assert n["손절가"].value < n["매수가"].value < n["매도가(목표가)"].value
    assert any("샘플" in e.value for e in at.error)  # 가짜 시세 경고
    at = _submit(at)  # 자동 채움 값 그대로 저장 가능(불변식 만족)
    assert not any("이미" in e.value or "호가" in e.value for e in at.error)
    assert row["code"] in _codes(app_db)


def test_object_unknown_code_leaves_fields_for_manual_input(app_db):
    at = _object_page(app_db)
    _button(at, "종목 추가").click()
    at.run()
    {t.label: t for t in at.text_input}["종목코드 (6자리)"].set_value("ZZZ999")
    at.run()
    assert not at.exception
    assert {t.label: t for t in at.text_input}["종목명"].value == ""


def test_object_autofill_prefers_api_name_and_sector(app_db, monkeypatch):
    class Api(_OfflineKiwoom):
        info = {"name": "API종목명", "sector": "API업종"}

    monkeypatch.setattr("engine.data.KiwoomQuote", lambda: Api())
    uni = load_universe()
    code = uni[~uni["code"].isin(_codes(app_db))].iloc[0]["code"]
    at = _object_page(app_db)
    _button(at, "종목 추가").click()
    at.run()
    {t.label: t for t in at.text_input}["종목코드 (6자리)"].set_value(code)
    at.run()
    f = {t.label: t for t in at.text_input}
    assert f["종목명"].value == "API종목명" and f["섹터"].value == "API업종"


def test_object_autofill_prev_close_from_api_then_current_price(app_db, monkeypatch):
    uni = load_universe()
    code = uni[~uni["code"].isin(_codes(app_db))].iloc[0]["code"]

    def fill(prev, cur):
        class Api(_OfflineKiwoom):
            def prev_close(self, c):
                return prev

            def latest_price(self, c):
                return cur

        monkeypatch.setattr("engine.data.KiwoomQuote", lambda: Api())
        at = _object_page(app_db)
        _button(at, "종목 추가").click()
        at.run()
        {t.label: t for t in at.text_input}["종목코드 (6자리)"].set_value(code)
        at.run()
        return at, {x.label: x for x in at.number_input}["전일종가"].value

    at, v = fill(91_000, 95_000)
    assert v == 91_000
    at, v = fill(None, 95_000)
    assert v == 95_000 and any("현재가" in w.value for w in at.warning)


def test_object_autofill_shows_api_failure_messages(app_db):
    uni = load_universe()
    code = uni[~uni["code"].isin(_codes(app_db))].iloc[0]["code"]
    at = _object_page(app_db)  # 오프라인 가짜 키움: 모든 조회가 빈 결과
    _button(at, "종목 추가").click()
    at.run()
    {t.label: t for t in at.text_input}["종목코드 (6자리)"].set_value(code)
    at.run()
    texts = " ".join(w.value for w in at.warning)
    assert "종목명을 조회하지 못했습니다" in texts and "전일종가를 조회하지 못했습니다" in texts
