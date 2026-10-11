"""로그인 계정 생성: python -m jobs.create_user <ID>   (비밀번호는 프롬프트로 입력)"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from engine import db

BASE = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("STOCK_DB", str(BASE / "data" / "app.db"))
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")

if SUPABASE_DB_URL:
    from engine import db_pg as db  # type: ignore[assignment]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("사용법: python -m jobs.create_user <ID>")
    pw = getpass.getpass("비밀번호(8자 이상): ")
    if pw != getpass.getpass("비밀번호 확인: "):
        sys.exit("비밀번호가 일치하지 않습니다.")
    conn = db.connect(SUPABASE_DB_URL or DB_PATH)
    db.create_user(conn, sys.argv[1], pw)
    print(f"계정 '{sys.argv[1]}' 저장 완료")


if __name__ == "__main__":
    main()
