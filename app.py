"""추천 종목 대시보드 (프로토타입). 실행: streamlit run app.py"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from engine import db, pricing
from engine.data import get_provider

BASE = Path(__file__).resolve().parent
DB_PATH = os.environ.get("STOCK_DB", str(BASE / "data" / "app.db"))
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
PROVIDER = os.environ.get("STOCK_PROVIDER", "sample")

if SUPABASE_DB_URL:
    from engine import db_pg as db  # type: ignore[assignment]
MAX_FAILS = 5

st.set_page_config(page_title="추천 종목", page_icon="📈", layout="wide")


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


# ───────────────────────── 화면 1: 로그인 ─────────────────────────
def render_login(conn) -> None:
    st.title("📈 추천 종목 대시보드")
    if db.user_count(conn) == 0:
        st.warning("등록된 계정이 없습니다. 터미널에서 `python -m jobs.create_user <ID>` 로 계정을 만들어 주세요.")
        return

    with st.form("login"):
        username = st.text_input("ID")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인")

    if submitted:
        fails = st.session_state.get("fails", 0)
        if fails >= MAX_FAILS:
            st.error("로그인 시도 횟수를 초과했습니다. 브라우저를 새로 열어 다시 시도해 주세요.")
        elif db.verify_user(conn, username, password):
            st.session_state["user"] = username
            st.session_state["fails"] = 0
            st.rerun()
        else:
            st.session_state["fails"] = fails + 1
            st.error("ID 또는 비밀번호가 올바르지 않습니다.")


# ───────────────────────── 화면 2: 추천 종목 ─────────────────────────
def build_table(recs, entries) -> pd.DataFrame:
    rows = []
    for r in recs:
        e = entries.get(r["code"])
        actual = e["actual_price"] if e else None
        current = e["current_price"] if e else None
        rows.append(
            {
                "종목": f"{r['name']} ({r['code']})",
                "의견": r["opinion"],
                "매수가": r["buy_price"],
                "매도가": r["sell_price"],
                "손절가": r["stop_price"],
                "실구매가": actual,
                "현재가": current,
                "수익률(%)": pricing.profit_rate(actual, current),
                "선정 사유": r["reason"],
            }
        )
    df = pd.DataFrame(rows)
    for col in ["매수가", "매도가", "손절가", "실구매가", "현재가", "수익률(%)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")  # None → 빈 칸
    return df


def render_main(conn) -> None:
    user = st.session_state["user"]
    with st.sidebar:
        st.write(f"**{user}** 님")
        if st.button("로그아웃"):
            st.session_state.pop("user", None)
            st.rerun()

    st.title("📈 오늘의 추천 종목")
    dates = db.run_dates(conn)
    if not dates:
        st.info("아직 생성된 추천이 없습니다. `python -m jobs.daily_job --demo` 를 실행해 주세요.")
        return

    run_date = st.selectbox("추천 기준일", dates)
    run = db.get_run(conn, run_date)
    regime = json.loads(run["regime_detail"])

    if run["data_source"] == "sample":
        st.error("⚠️ 샘플(가짜) 시세와 가상의 뉴스로 계산한 화면입니다. 실제 시장 데이터가 아니며 투자 판단에 쓰면 안 됩니다.")
    c1, c2 = st.columns(2)
    c1.metric("시장 국면", run["regime"], f"지수 vs 20일선 {regime['gap_pct']:+.1f}%")
    c2.metric("데이터 출처", run["data_source"])

    recs = db.get_recommendations(conn, run_date)
    if not recs:
        st.info("이 날짜에는 추천 종목이 없습니다.")
        return

    entries = db.get_entries(conn, user, run_date)
    df = build_table(recs, entries)

    st.caption("실구매가·현재가는 직접 입력하세요. 둘 다 있으면 수익률이 계산되고, 없으면 공란입니다. 입력 후 [저장]을 누르세요.")
    edited = st.data_editor(
        df,
        key=f"editor_{run_date}",
        hide_index=True,
        width="stretch",
        disabled=["종목", "의견", "매수가", "매도가", "손절가", "수익률(%)", "선정 사유"],
        column_config={
            "매수가": st.column_config.NumberColumn(format="%d"),
            "매도가": st.column_config.NumberColumn(format="%d", help="매수 의견=목표가, 매도 의견=반등 시 매도 기준가"),
            "손절가": st.column_config.NumberColumn(format="%d"),
            "실구매가": st.column_config.NumberColumn(format="%d", min_value=0, step=1),
            "현재가": st.column_config.NumberColumn(format="%d", min_value=0, step=1),
            "수익률(%)": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

    col_save, col_fill, _ = st.columns([1, 2, 6])
    if col_save.button("저장", type="primary"):
        for r, (_, row) in zip(recs, edited.iterrows()):
            db.save_entry(conn, user, run_date, r["code"], _num(row["실구매가"]), _num(row["현재가"]))
        st.rerun()
    if col_fill.button("현재가 자동 채우기"):
        provider = get_provider(PROVIDER)
        for r, (_, row) in zip(recs, edited.iterrows()):
            price = provider.latest_price(r["code"])
            db.save_entry(conn, user, run_date, r["code"], _num(row["실구매가"]), price if price else _num(row["현재가"]))
        st.rerun()

    st.caption("본 화면은 판단 보조 도구이며 수익을 보장하지 않습니다. 투자 판단과 책임은 본인에게 있습니다.")


def main() -> None:
    conn = db.connect(SUPABASE_DB_URL or DB_PATH)
    try:
        if st.session_state.get("user"):
            render_main(conn)
        else:
            render_login(conn)
    finally:
        conn.close()


main()
