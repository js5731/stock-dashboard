# 추천 종목 대시보드 (프로토타입)

로그인 → 추천 종목 표(매수가·매도가·손절가·의견·실구매가·현재가·수익률)를 보여주는 Streamlit 앱.
**기본값은 샘플(가짜) 시세 + 가상의 뉴스**입니다. 실제 시장 데이터가 아니며 투자 판단에 쓰면 안 됩니다.

## 실행 (3줄)

```bash
pip install -r requirements.txt
python -m jobs.daily_job --demo      # 샘플 추천 생성 + 데모 계정(demo / demo1234) 생성
streamlit run app.py                 # http://localhost:8501
```

데모 계정은 테스트 전용입니다. 내 계정은 `python -m jobs.create_user <ID>` 로 만들고, `data/app.db`에서 `demo`를 삭제하세요.

## 매일 쓰는 방법 (뉴스 직접 붙여넣기)

1. 전날 시장 뉴스 헤드라인을 `my_news.txt`에 한 줄에 하나씩 저장
2. `python -m jobs.daily_job --news-file my_news.txt` (국면 수동 지정: `--regime 방어`)
3. 앱에서 표를 보고 실구매가·현재가 입력 → [저장] (수익률 자동 계산, 값이 없으면 공란)

## 구조

| 파일 | 역할 |
|---|---|
| `app.py` | 화면 1(로그인), 화면 2(추천 표) |
| `engine/pricing.py` | 호가단위, ATR, 매수·목표·손절가, 수익률 |
| `engine/selection.py` | 뉴스 점수 → 시장 국면 → 안전 필터 → 종합 점수 |
| `engine/data.py` | 시세 제공자 (`sample` 기본, `fdr` 선택) |
| `engine/db.py` | SQLite 저장소, 비밀번호 해시(PBKDF2) |
| `jobs/daily_job.py` | 매일 아침 배치 |
| `data/keywords.csv` | 뉴스 키워드 → 섹터 → 방향·가중치 (직접 편집) |
| `data/universe.csv` | 종목-섹터 매핑 (샘플 20종목, 직접 편집) |
| `data/blocklist.csv` | 제외할 종목코드(관리·투자경고 등) |

## 계산 규칙 (모두 검증 전 가설)

- 매수가 = 전일종가 − 0.3×ATR, 목표가 = 매수가 + 1.5×ATR, 손절가 = 매수가 − 1.0×ATR (방어장은 0.7×ATR)
- 호가단위 반올림, 상·하한가(±30%) 범위 내로 제한
- 종합 점수 = 뉴스 40% + 거래량 20% + 변동성 20% + 국면 적합도 20%
- 국면: 지수가 20일선 위면 공격(최대 5종목), 아니면 방어(최대 3종목)
- 매도/회피 의견은 매수가·손절가 없이 "반등 시 매도 기준가"만 표시

## 테스트

```bash
python -m pytest -q     # 44개
```

## 한계 (프로토타입)

- 샘플 시세는 난수로 만든 값이라 추천 결과에 투자 의미가 없습니다.
- 실시세(`--provider fdr`)는 구현만 되어 있고 개발 환경에서 검증하지 못했습니다.
- 토스증권 API 연동, saveticker 뉴스 자동 수집은 포함하지 않았습니다 (약관·API 문서 확인 후 추가).
- 시장 국면은 지수 20일선만 사용합니다 (환율·미국 선물 미반영).
- 로그인 시도 제한은 브라우저 세션 단위라 보안 기능으로 충분하지 않습니다. 공개 전 Supabase Auth 등으로 교체하세요.
- SQLite는 GitHub Actions 같은 임시 환경에서 유지되지 않으므로 무료 배포 시 Supabase(Postgres)로 교체해야 합니다.
- 호가단위 표는 KRX 코스피/코스닥 공통 기준이며 ETF/ETN은 다릅니다. 최신 표는 KRX에서 확인하세요.
