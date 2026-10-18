"""추천 종목 대시보드 (프로토타입). 실행: streamlit run app.py"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from engine import db, manage, pricing
from engine.data import get_provider, load_universe

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")  # 환경변수가 이미 있으면 덮어쓰지 않음
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
    st.title("📈 CoreService project1")
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
            st.session_state["role"] = db.get_role(conn, username)
            st.session_state["fails"] = 0
            st.rerun()
        else:
            st.session_state["fails"] = fails + 1
            st.error("ID 또는 비밀번호가 올바르지 않습니다.")


# ───────────────────────── 화면 2: 추천 종목 ─────────────────────────
LABEL_ACTIVE = "🔵 추천"
LABEL_REMOVED = "🟠 관리자 삭제(보유중)"
ROW_COLORS = {LABEL_ACTIVE: "", LABEL_REMOVED: "background-color: rgba(255, 152, 0, 0.18)"}


def build_table(recs, entries) -> pd.DataFrame:
    rows = []
    for r in recs:
        e = entries.get(r["code"])
        actual = e["actual_price"] if e else None
        current = e["current_price"] if e else None
        rows.append(
            {
                "구분": LABEL_REMOVED if r["removed"] else LABEL_ACTIVE,
                "종목": f"{r['name']} ({r['code']})",
                "의견": r["opinion"],
                "매수가": r["buy_price"],
                "매도가": r["sell_price"],
                "손절가": r["stop_price"],
                "실구매가": actual,
                "현재가": current,
                "수익률(%)": pricing.profit_rate(actual, current),
                "선정 사유": r["reason"],
                "비고": (e["note"] if e else None) or "",
            }
        )
    df = pd.DataFrame(rows)
    for col in ["매수가", "매도가", "손절가", "실구매가", "현재가", "수익률(%)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")  # None → 빈 칸
    return df


def render_overview(conn) -> None:
    user = st.session_state["user"]
    st.title("*Today Reprot")
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

    recs = db.get_user_recommendations(conn, user, run_date)
    if not recs:
        st.info("이 날짜에는 추천 종목이 없습니다.")
        return

    entries = db.get_entries(conn, user, run_date)
    df = build_table(recs, entries)

    if any(r["removed"] for r in recs):
        st.info("🟠 표시 종목은 관리자가 추천에서 삭제했지만, 실구매가를 입력하셔서 계속 보여 드립니다.")
    st.info("💾 실구매가·현재가·비고를 입력한 뒤 반드시 아래 **[저장]** 버튼을 눌러야 DB에 저장됩니다. 저장하지 않고 화면을 닫거나 기준일을 바꾸면 입력값이 사라집니다.")
    st.caption("저장 후 실구매가와 현재가가 둘 다 있으면 수익률이 계산되고, 하나라도 없으면 공란입니다.")
    editor_key = f"editor_{run_date}"

    edited = st.data_editor(
        df.style.apply(lambda row: [ROW_COLORS[row["구분"]]] * len(row), axis=1),
        key=editor_key,
        hide_index=True,
        width="stretch",
        disabled=["구분", "종목", "의견", "매수가", "매도가", "손절가", "수익률(%)", "선정 사유"],
        column_config={
            "매수가": st.column_config.NumberColumn(format="%d"),
            "매도가": st.column_config.NumberColumn(format="%d", help="매수 의견=목표가, 매도 의견=반등 시 매도 기준가"),
            "손절가": st.column_config.NumberColumn(format="%d"),
            "실구매가": st.column_config.NumberColumn(format="%d", min_value=0, step=1),
            "현재가": st.column_config.NumberColumn(format="%d", min_value=0, step=1),
            "수익률(%)": st.column_config.NumberColumn(format="%.2f%%"),
            "비고": st.column_config.TextColumn(help="자유롭게 메모하세요.", max_chars=200),
        },
    )

    col_save, col_fill, _ = st.columns([1, 1, 6])
    if col_save.button("저장", type="primary"):
        for r, (_, row) in zip(recs, edited.iterrows()):
            db.save_entry(conn, user, run_date, r["code"], _num(row["실구매가"]), _num(row["현재가"]), row["비고"])
        st.session_state["saved_msg"] = "저장되었습니다."
        st.rerun()
    if col_fill.button("조회", help="키움 API로 종목별 현재가를 조회해 현재가 칸에 저장합니다."):
        try:
            provider = get_provider("kiwoom")
        except RuntimeError as e:
            st.error(f"{e} `.env`에 키움 API 키를 설정해 주세요.")
        else:
            failed = []
            for r, (_, row) in zip(recs, edited.iterrows()):
                price = provider.latest_price(r["code"])
                if price is None:
                    failed.append(r["name"])
                db.save_entry(conn, user, run_date, r["code"], _num(row["실구매가"]), price if price else _num(row["현재가"]), row["비고"])
            if failed:
                st.session_state["query_warn"] = "현재가 조회 실패: " + ", ".join(failed)
            st.rerun()
    if msg := st.session_state.pop("saved_msg", None):
        st.success(msg)
    if msg := st.session_state.pop("query_warn", None):
        st.warning(msg)

    st.caption("본 화면은 판단 보조 도구이며 수익을 보장하지 않습니다. 투자 판단과 책임은 본인에게 있습니다.")


# ───────────────────────── 메뉴: Overview / Analysis / Object ─────────────────────────
MENUS = {"Overview": "user", "Analysis": "admin", "Object": "admin"}  # 메뉴 → 필요한 최소 역할


def allowed_menus(role: str) -> list[str]:
    return list(MENUS) if role == "admin" else [m for m, need in MENUS.items() if need == "user"]


def render_menu(conn) -> None:
    user, role = st.session_state["user"], st.session_state.get("role", "user")
    menus = allowed_menus(role)
    with st.sidebar:
        st.write(f"**{user}** 님" + (" (관리자)" if role == "admin" else ""))
        menu = st.radio("Menu", menus, key="menu")
        if st.button("로그아웃"):
            for k in ("user", "role", "menu"):
                st.session_state.pop(k, None)
            st.rerun()
    if menu not in menus:  # 서버 측 재확인: 화면에서 숨기는 것만으로는 권한 통제가 아니다
        st.error("접근 권한이 없습니다.")
        return
    {"Overview": render_overview, "Analysis": render_analysis, "Object": render_object}[menu](conn)


def _resolve_stock(conn, query: str):
    """종목명/코드 → (code, name, sector). universe.csv와 추천 이력에서 찾는다."""
    q = query.strip()
    uni = load_universe()
    hit = uni[(uni["code"] == q) | (uni["name"] == q)]
    if hit.empty:
        hit = uni[uni["name"].str.contains(q, regex=False)]
    if len(hit):
        r = hit.iloc[0]
        return r["code"], r["name"], r["sector"]
    hist = db.code_history(conn, q) if q.isalnum() and len(q) == 6 else []
    if hist:
        return hist[0]["code"], hist[0]["name"], hist[0]["sector"]
    return None


def render_analysis(conn) -> None:
    st.title("🔍 Analysis")
    query = st.text_input("종목명 또는 종목코드", placeholder="예: 삼성전자 또는 005930")
    if not query.strip():
        st.info("종목을 입력하면 기업 개요·주가·추천 이력을 보여줍니다.")
        return
    stock = _resolve_stock(conn, query)
    if stock is None:
        st.warning("등록된 종목이 아닙니다. `data/universe.csv` 또는 Object 메뉴에 등록된 종목만 조회할 수 있습니다.")
        return
    code, name, sector = stock
    provider = get_provider(PROVIDER)
    if not provider.is_real:
        st.error("⚠️ 샘플(가짜) 시세입니다. 실제 시장 데이터가 아니며 투자 판단에 쓰면 안 됩니다.")

    hist = provider.history(code, 120)
    close = hist["close"]
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    st.subheader(f"{name} ({code})")
    st.caption(f"섹터: {sector}")

    t1, t2, t3 = st.tabs(["기업 현황", "주가·차트", "추천 이력"])
    with t1:
        c = st.columns(4)
        c[0].metric("종가", f"{last:,.0f}원", f"{(last / prev - 1) * 100:+.2f}%")
        c[1].metric("52주(최대 120일) 최고", f"{close.max():,.0f}원")
        c[2].metric("최저", f"{close.min():,.0f}원")
        c[3].metric("20일선 대비", f"{(last / close.tail(20).mean() - 1) * 100:+.1f}%")
        st.caption("재무제표·컨센서스 등 wisereport의 재무 항목은 제공 출처가 없어 아직 연동하지 않았습니다 (이용약관 확인 필요).")
    with t2:
        chart = hist.set_index("date")[["close"]].assign(MA20=close.rolling(20).mean().values)
        st.line_chart(chart)
        st.dataframe(hist.tail(20).iloc[::-1], hide_index=True, width="stretch")
    with t3:
        rows = db.code_history(conn, code)
        if not rows:
            st.info("이 종목의 추천 이력이 없습니다.")
        else:
            st.dataframe(
                pd.DataFrame([{k: r[k] for k in ("run_date", "opinion", "buy_price", "sell_price", "stop_price", "reason")} for r in rows]),
                hide_index=True, width="stretch",
            )


def render_object(conn) -> None:
    st.title("🗂 Object")
    st.caption("Overview에 표시되는 추천 종목을 관리합니다. 실구매가·현재가·수익률은 사용자별 입력이라 여기서 다루지 않습니다.")
    dates = db.run_dates(conn)
    if not dates:
        st.info("아직 생성된 추천이 없습니다. `python -m jobs.daily_job --demo` 를 실행해 주세요.")
        return
    run_date = st.selectbox("추천 기준일", dates, key="obj_date")
    recs = db.get_recommendations(conn, run_date)

    cols = ["code", "name", "sector", "opinion", "score", "news_score", "buy_price", "sell_price", "stop_price", "prev_close", "reason"]
    st.dataframe(pd.DataFrame([{c: r[c] for c in cols} for r in recs], columns=cols), hide_index=True, width="stretch")

    st.subheader("종목 추가 / 수정")
    st.caption("이미 있는 종목코드를 입력하면 해당 종목을 수정합니다. 매수가·손절가는 매도/회피 의견이면 0(공란)으로 두세요.")
    with st.form("obj_form", clear_on_submit=False):
        a, b, c = st.columns(3)
        code = a.text_input("종목코드 (6자리)")
        name = b.text_input("종목명")
        sector = c.text_input("섹터")
        opinion = a.selectbox("의견", manage.OPINIONS)
        score = b.number_input("종합 점수", value=0.0, step=0.1)
        news_score = c.number_input("뉴스 점수", value=0.0, step=0.1)
        prev_close = a.number_input("전일종가", min_value=0, step=1)
        buy = b.number_input("매수가", min_value=0, step=1)
        sell = c.number_input("매도가(목표가)", min_value=0, step=1)
        stop = a.number_input("손절가", min_value=0, step=1)
        reason = st.text_input("선정 사유")
        submitted = st.form_submit_button("저장", type="primary")
    if submitted:
        rec = {
            "code": code.strip(), "name": name.strip(), "sector": sector.strip(), "opinion": opinion,
            "score": score, "news_score": news_score, "prev_close": int(prev_close) or None,
            "buy_price": int(buy) or None, "sell_price": int(sell) or None, "stop_price": int(stop) or None,
            "reason": reason.strip(),
        }
        errors = manage.validate_recommendation(rec)
        if errors:
            for e in errors:
                st.error(e)
        else:
            db.upsert_recommendation(conn, run_date, rec)
            st.success(f"{rec['name']} ({rec['code']}) 저장 완료")
            st.rerun()

    if recs:
        st.subheader("종목 삭제")
        target = st.selectbox(
            "삭제할 종목", [f"{r['name']} ({r['code']})" for r in recs], index=None, placeholder="종목을 선택하세요", key="obj_del"
        )
        if st.button("삭제", disabled=target is None, help="실구매가를 입력한 사용자에게는 계속 보이고(관리자 삭제 표시), 그 외 사용자에게서는 사라집니다."):
            gone = db.delete_recommendation(conn, run_date, target.rsplit("(", 1)[1].rstrip(")"))
            st.session_state["obj_msg"] = "삭제 완료" if gone else "삭제 처리했습니다. 실구매가를 입력한 사용자 화면에는 계속 표시됩니다."
            st.rerun()
    if msg := st.session_state.pop("obj_msg", None):
        st.info(msg)


def main() -> None:
    conn = db.connect(SUPABASE_DB_URL or DB_PATH)
    try:
        if st.session_state.get("user"):
            render_menu(conn)
        else:
            render_login(conn)
    finally:
        conn.close()


main()
