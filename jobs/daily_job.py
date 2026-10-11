"""매일 아침 실행하는 배치: 뉴스 → 종목 선정 → 가격 산출 → DB 저장.

사용:
  python -m jobs.daily_job --demo                       # 샘플 시세/뉴스 + 데모 계정 생성
  python -m jobs.daily_job --news-file my_news.txt      # 직접 붙여넣은 헤드라인 사용
  python -m jobs.daily_job --news-file n.txt --provider fdr   # 실제 시세(FinanceDataReader)
"""
from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from engine import db
from engine.data import DATA_DIR, get_provider
from engine.selection import build_recommendations, parse_headlines

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")  # 환경변수가 이미 있으면 덮어쓰지 않음
DEFAULT_DB = os.environ.get("STOCK_DB", str(BASE / "data" / "app.db"))
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")

if SUPABASE_DB_URL:
    from engine import db_pg as db  # type: ignore[assignment]
DEMO_USER, DEMO_PASSWORD = "demo", "demo1234"


def run(db_path: str, news_text: str, provider_name: str, regime_override: str | None, run_date: date) -> list[dict]:
    provider = get_provider(provider_name, end=run_date) if provider_name == "sample" else get_provider(provider_name)
    recs, meta = build_recommendations(
        parse_headlines(news_text), provider, run_date, regime_override=regime_override
    )
    conn = db.connect(SUPABASE_DB_URL or db_path)
    db.save_run(conn, run_date.isoformat(), meta["regime"], provider.name, recs)
    conn.close()

    print(f"[{run_date}] 국면={meta['regime']['mode']} (지수 {meta['regime']['last']} / MA20 {meta['regime']['ma']})")
    for r in recs:
        print(f"  {r['opinion']:<6} {r['name']:<12} 점수 {r['score']:>5}  매수 {r['buy_price']}  매도 {r['sell_price']}  손절 {r['stop_price']}")
    for code, name, why in meta["excluded"]:
        print(f"  제외: {name}({code}) - {why}")
    if not recs:
        print("  추천 종목 없음 (뉴스가 매핑 키워드와 맞지 않거나 신호가 약함)")
    return recs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--news-file")
    ap.add_argument("--provider", default="sample", choices=["sample", "fdr"])
    ap.add_argument("--regime", choices=["공격", "방어"], help="국면 수동 지정(선택)")
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--demo", action="store_true", help="샘플 뉴스 사용 + 데모 계정(demo/demo1234) 생성")
    args = ap.parse_args()

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    if args.demo:
        news_path = DATA_DIR / "sample_news.txt"
        conn = db.connect(SUPABASE_DB_URL or args.db)
        db.create_user(conn, DEMO_USER, DEMO_PASSWORD)
        conn.close()
        print(f"데모 계정 생성: {DEMO_USER} / {DEMO_PASSWORD}  (테스트 전용, 반드시 삭제/변경)")
    elif args.news_file:
        news_path = Path(args.news_file)
    else:
        ap.error("--demo 또는 --news-file 이 필요합니다.")

    run(args.db, news_path.read_text(encoding="utf-8"), args.provider, args.regime, run_date)


if __name__ == "__main__":
    main()
