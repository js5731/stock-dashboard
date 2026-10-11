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


def get_provider(name: str = "sample", **kwargs):
    if name == "fdr":
        return FdrProvider()
    return SampleProvider(**kwargs)
