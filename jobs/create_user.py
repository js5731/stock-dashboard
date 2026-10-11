"""로그인 계정 생성: python -m jobs.create_user <ID>   (비밀번호는 프롬프트로 입력)"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from engine import db

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")  # 환경변수가 이미 있으면 덮어쓰지 않음
DB_PATH = os.environ.get("STOCK_DB", str(BASE / "data" / "app.db"))
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")

if SUPABASE_DB_URL:
    from engine import db_pg as db  # type: ignore[assignment]


def main() -> None:
    args = sys.argv[1:]
    role = None
    if len(args) == 3 and args[1] == "--role":
        role = args[2]
        args = args[:1]
    if len(args) != 1 or (role is not None and role not in db.ROLES):
        sys.exit("사용법: python -m jobs.create_user <ID> [--role user|admin]")
    pw = getpass.getpass("비밀번호(8자 이상): ")
    if pw != getpass.getpass("비밀번호 확인: "):
        sys.exit("비밀번호가 일치하지 않습니다.")
    conn = db.connect(SUPABASE_DB_URL or DB_PATH)
    db.create_user(conn, args[0], pw, role)
    print(f"계정 '{args[0]}' 저장 완료 (role={db.get_role(conn, args[0])})")


if __name__ == "__main__":
    main()
