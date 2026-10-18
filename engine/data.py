"""시세 제공자. 프로토타입 기본값은 '샘플'(가짜 시세)이며 실제 시세가 아니다.

provider 인터페이스:
  history(code, days) -> DataFrame[date, open, high, low, close, volume]  (오름차순)
  index_history(days) -> 위와 동일 (코스피 대용)
  latest_price(code)  -> int | None
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"


def load_universe(path: Path | None = None) -> pd.DataFrame:
    return pd.read_csv(path or DATA_DIR / "universe.csv", dtype={"code": str})


def _seed(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)


def _business_days(end: date, n: int) -> list[date]:
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _tick_round(price: float) -> float:
    from .pricing import round_to_tick

    return float(round_to_tick(price))


class SampleProvider:
    """종목코드로 시드를 고정한 가짜 일봉. 같은 입력이면 항상 같은 결과."""

    name = "sample"
    is_real = False

    def __init__(self, universe: pd.DataFrame | None = None, end: date | None = None):
        self.universe = universe if universe is not None else load_universe()
        self.end = end or date.today()

    def _base_price(self, code: str) -> float:
        row = self.universe[self.universe["code"] == code]
        return float(row["sample_price"].iloc[0]) if len(row) else 50_000.0

    def _series(self, key: str, base: float, days: int, vol_base: float, spike: bool) -> pd.DataFrame:
        rng = np.random.RandomState(_seed(key))
        daily_vol = rng.uniform(0.012, 0.03)
        rets = rng.normal(0.0003, daily_vol, days)
        close = base * np.exp(np.cumsum(rets))
        close = close / close[-1] * base  # 마지막 값이 base가 되도록
        open_ = close * (1 + rng.normal(0, daily_vol / 3, days))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, daily_vol / 2, days)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, daily_vol / 2, days)))
        volume = vol_base * rng.lognormal(0, 0.35, days)
        if spike:
            volume[-1] *= rng.uniform(1.8, 3.2)
        return pd.DataFrame(
            {
                "date": _business_days(self.end, days),
                "open": open_.round(0),
                "high": high.round(0),
                "low": low.round(0),
                "close": close.round(0),
                "volume": volume.round(0),
            }
        )

    def history(self, code: str, days: int = 60) -> pd.DataFrame:
        base = self._base_price(code)
        row = self.universe[self.universe["code"] == code]
        vol_base = float(row["sample_volume"].iloc[0]) if len(row) else 500_000.0
        spike = _seed(code + "spike") % 3 == 0
        return self._series(code, base, days, vol_base, spike)

    def index_history(self, days: int = 60) -> pd.DataFrame:
        return self._series("KOSPI-INDEX", 2_700.0, days, 400_000_000.0, False)

    def latest_price(self, code: str) -> Optional[int]:
        return int(self.history(code, 30)["close"].iloc[-1])


class FdrProvider:
    """FinanceDataReader 기반 실제 시세(선택). 설치: pip install finance-datareader
    ※ 이 프로토타입 개발 환경에서는 외부 접속이 제한되어 동작을 검증하지 못했다."""

    name = "fdr"
    is_real = True

    def __init__(self):
        import FinanceDataReader as fdr  # noqa: N813

        self._fdr = fdr

    def _fetch(self, symbol: str, days: int) -> pd.DataFrame:
        start = date.today() - timedelta(days=int(days * 1.8) + 10)
        raw = self._fdr.DataReader(symbol, start.isoformat())
        df = raw.reset_index().rename(
            columns={"Date": "date", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        )
        return df[["date", "open", "high", "low", "close", "volume"]].tail(days).reset_index(drop=True)

    def history(self, code: str, days: int = 60) -> pd.DataFrame:
        return self._fetch(code, days)

    def index_history(self, days: int = 60) -> pd.DataFrame:
        return self._fetch("KS11", days)

    def latest_price(self, code: str) -> Optional[int]:
        try:
            return int(self._fetch(code, 5)["close"].iloc[-1])
        except Exception:
            return None


class KiwoomQuote:
    """키움 REST API 현재가 조회 전용 (ka10001 주식기본정보요청). 시세 이력은 제공하지 않는다.
    환경변수: KIWOOM_APPKEY, KIWOOM_SECRETKEY, KIWOOM_BASE_URL(기본 실서버, 모의투자는 https://mockapi.kiwoom.com)
    ※ 문서 기준으로 작성했으며 실제 키로 호출 검증은 하지 못했다. 조회만 하며 주문 기능은 없다."""

    name = "kiwoom"
    is_real = True
    DEFAULT_URL = "https://api.kiwoom.com"

    def __init__(self, appkey: str | None = None, secretkey: str | None = None, base_url: str | None = None):
        import os

        self.appkey = appkey or os.environ.get("KIWOOM_APPKEY", "")
        self.secretkey = secretkey or os.environ.get("KIWOOM_SECRETKEY", "")
        self.base_url = (base_url or os.environ.get("KIWOOM_BASE_URL") or self.DEFAULT_URL).rstrip("/")
        if not self.appkey or not self.secretkey:
            raise RuntimeError("KIWOOM_APPKEY / KIWOOM_SECRETKEY 환경변수가 설정되지 않았습니다.")
        self._token: str | None = None

    def _post(self, path: str, body: dict, headers: dict | None = None) -> dict:
        import json
        import urllib.request

        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json;charset=UTF-8", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def _get_token(self) -> str:
        if not self._token:
            res = self._post(
                "/oauth2/token",
                {"grant_type": "client_credentials", "appkey": self.appkey, "secretkey": self.secretkey},
            )
            if not res.get("token"):
                raise RuntimeError(f"키움 토큰 발급 실패: {res.get('return_msg', res)}")
            self._token = res["token"]
        return self._token

    def latest_price(self, code: str) -> Optional[int]:
        """현재가(원). 조회 실패 시 None. 응답의 부호(+/-)는 전일대비 방향이므로 제거한다."""
        try:
            res = self._post(
                "/api/dostk/stkinfo",
                {"stk_cd": code},
                {"authorization": f"Bearer {self._get_token()}", "api-id": "ka10001"},
            )
            if res.get("return_code", 0) != 0:
                return None
            price = abs(int(str(res["cur_prc"]).replace(",", "")))
            return price or None
        except Exception:
            return None

    def prev_close(self, code: str) -> Optional[int]:
        """전일종가(원). ka10001 응답의 기준가(base_pric)를 쓴다. 조회 실패 시 None.
        ※ base_pric 가 전일종가라는 것은 문서 기억에 의존한 추정이며 실제 호출 검증은 못 했다."""
        try:
            res = self._post(
                "/api/dostk/stkinfo",
                {"stk_cd": code},
                {"authorization": f"Bearer {self._get_token()}", "api-id": "ka10001"},
            )
            if res.get("return_code", 0) != 0:
                return None
            return abs(int(str(res["base_pric"]).replace(",", ""))) or None
        except Exception:
            return None

    def stock_info(self, code: str) -> dict:
        """종목명·업종(섹터) 조회 (ka10100 종목정보 조회). 찾지 못하면 빈 dict.
        ※ 응답 필드명(name, upName)은 공식 문서로 확인하지 못한 추정이며 실제 호출 검증도 못 했다.
        업종을 못 받으면 종목명만 ka10001의 stk_nm 으로 보완한다."""
        headers = lambda api: {"authorization": f"Bearer {self._get_token()}", "api-id": api}  # noqa: E731
        out: dict = {}
        try:
            res = self._post("/api/dostk/stkinfo", {"stk_cd": code}, headers("ka10100"))
            if res.get("return_code", 0) == 0:
                out = {"name": (res.get("name") or "").strip(), "sector": (res.get("upName") or "").strip()}
        except Exception:
            pass
        if not out.get("name"):
            try:
                res = self._post("/api/dostk/stkinfo", {"stk_cd": code}, headers("ka10001"))
                if res.get("return_code", 0) == 0 and res.get("stk_nm"):
                    out["name"] = str(res["stk_nm"]).strip()
            except Exception:
                pass
        return {k: v for k, v in out.items() if v}


def get_provider(name: str = "sample", **kwargs):
    if name == "kiwoom":
        return KiwoomQuote(**kwargs)
    if name == "fdr":
        return FdrProvider()
    return SampleProvider(**kwargs)
