# CLAUDE.md — 추천 종목 대시보드

## 프로젝트 개요
뉴스 헤드라인으로 국내주식 추천 종목을 고르고, 매수·매도·손절가를 계산해 보여주는 **개인용** 웹앱 (Streamlit + SQLite).
화면 1: 로그인 / 화면 2: 추천 표(의견, 매수가, 매도가, 손절가, 실구매가, 현재가, 수익률).
목표는 **무료 운영**. 매일 아침 배치가 미리 계산해 DB에 저장하고, 화면은 읽기만 한다.
현재 단계: 프로토타입(샘플 시세). 실제 돈을 넣기 전에 백테스트와 모의 운영 검증이 선행되어야 한다.

## 명령어
```bash
pip install -r requirements.txt
python -m jobs.daily_job --demo                    # 샘플 추천 + 데모 계정(demo/demo1234)
python -m jobs.daily_job --news-file news.txt      # 직접 붙여넣은 헤드라인으로 실행
python -m jobs.create_user <ID>                    # 계정 생성 (비밀번호는 프롬프트)
streamlit run app.py                               # http://localhost:8501
python -m pytest -q                                # 전체 테스트 (44개)
```

## 구조
- `app.py` — 로그인 + 추천 표 (data_editor로 실구매가·현재가 입력 후 [저장])
- `engine/pricing.py` — 호가단위, ATR, 매수·목표·손절가, 수익률 (순수 함수)
- `engine/selection.py` — 뉴스 점수 → 시장 국면 → 안전 필터 → 종합 점수 → 가격 산출
- `engine/data.py` — 시세 제공자: `SampleProvider`(기본, 가짜 시세), `FdrProvider`(선택, 미검증)
- `engine/db.py` — SQLite 스키마, PBKDF2 비밀번호 해시, 추천·사용자 입력 저장
- `jobs/` — 매일 배치(`daily_job`), 계정 생성(`create_user`)
- `data/keywords.csv` 뉴스 키워드→섹터→방향·가중치, `universe.csv` 종목-섹터, `blocklist.csv` 제외 종목
- `tests/` — pricing, selection, db 단위 테스트 + Streamlit AppTest

## 핵심 규칙 (수정 시 반드시 지킬 것)
1. **샘플 데이터 경고 유지**: 시세가 `sample`이면 화면에 가짜 데이터 경고를 반드시 표시한다. 제거하지 말 것.
2. **자동 주문 금지**: 이 앱은 추천을 보여주는 판단 보조 도구다. 주문 기능을 추가하지 않는다.
3. **비밀 정보**: API 키·비밀번호를 코드, 테스트, 커밋, 채팅에 넣지 않는다 (환경변수/Secrets 사용). 비밀번호는 평문 저장 금지.
4. **사용자별 데이터 분리**: `user_entries`는 항상 `username`으로 필터한다. 다른 사용자의 실구매가가 보이면 안 된다.
5. **수익률 공란 규칙**: 실구매가 또는 현재가가 없으면 수익률은 공란(None/NaN). 0이나 임의 값으로 채우지 않는다.
6. **가격 불변식**: 모든 가격은 호가단위 배수이고 상·하한가(±30%) 안이며, 매수 의견은 `손절가 < 매수가 < 목표가`를 만족해야 한다. 매도/회피 의견은 매수가·손절가가 None이다.
7. **가중치·계수 변경**: `selection.py`의 `WEIGHTS`, `SIGNAL_THRESHOLD`, `REGIME_PARAMS`와 `pricing.py`의 ATR 계수는 검증 전 가설값이다. 바꿀 때는 근거(백테스트 결과)를 남기고 관련 테스트를 함께 갱신한다.
8. **기준 예시 테스트 유지**: 전일종가 50,000 / ATR 1,500 → 매수 49,550 / 목표 51,800 / 손절 48,050 (`test_worked_example_from_plan`).

## 코딩 규칙
- Python 3.11+, 타입 힌트와 `from __future__ import annotations` 사용. 계산 로직은 `engine/`에 두고 `app.py`에는 화면 코드만 둔다.
- `engine/`은 Streamlit에 의존하지 않는다 (테스트와 배치에서 재사용).
- 기능을 바꾸면 `python -m pytest -q`를 실행해 전부 통과시킨 뒤 마친다. 새 규칙에는 테스트를 추가한다.
- Streamlit 1.50+ 기준 (`width="stretch"` 사용, `use_container_width` 사용 금지).
- 한국 시장 시간: 배치는 평일 아침(한국시간 07:30 전후)에 실행. GitHub Actions cron은 UTC이므로 `30 22 * * 0-4`.

## 알려진 한계 / 다음 작업
- 시세는 기본이 가짜(난수) 데이터. `FdrProvider`는 구현만 되어 있고 실행 검증을 못 했다.
- 토스증권 오픈API 연동 없음 (문서의 호출 한도·약관 확인 후 추가). saveticker 뉴스 자동 수집 없음 (이용약관 확인 필요, 불가 시 수동 붙여넣기).
- 시장 국면은 지수 20일선만 사용 (환율·미국 선물 미반영).
- 로그인 시도 제한은 세션 단위라 보안으로 불충분 → 공개 전 Supabase Auth 등으로 교체.
- SQLite는 임시 실행 환경에서 유지되지 않음 → 무료 배포 시 Supabase(Postgres)로 교체.
- 호가단위 표는 코스피/코스닥 공통 기준 (ETF/ETN 제외). KRX 최신 표 확인 필요.
- 백테스트와 4주 이상 모의 운영을 통과하기 전에는 실전 투자에 사용하지 않는다.

## 작업 방식
- 사용자는 SAP ABAP 개발 경력 20년의 개발자다. 설명은 한국어로, 결론 먼저, 근거와 실행 방법 순서로 간결하게.
- 사실·추정·의견을 구분하고, 확인하지 못한 것은 확인하지 못했다고 말한다 (예: 실시세 연동, 외부 사이트 약관).
- 투자 수익을 보장하는 표현을 쓰지 않는다. 법률·세무·투자자문 판단이 필요한 사안(타인 공개 서비스 등)은 전문가 확인을 권한다.
