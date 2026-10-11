import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# .env 자동 로드가 있어도 테스트는 항상 SQLite만 쓴다 (운영 DB 보호).
# 모듈이 임포트 시점에 환경변수를 읽으므로 테스트 모듈 수집 전에 비워 둔다. 빈 값은 load_dotenv가 덮어쓰지 않는다.
os.environ["SUPABASE_DB_URL"] = ""
