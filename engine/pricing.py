"""가격 계산 엔진: 호가단위, ATR, 매수/매도/손절가 산출.

기준(모두 검증 전 '가설'이며 6단계 백테스트로 조정해야 함):
  매수가 = 전일종가 - k_entry  * ATR   (기본 0.3)
  목표가 = 매수가   + k_target * ATR   (기본 1.5)
  손절가 = 매수가   - k_stop   * ATR   (기본 1.0)
호가단위는 KRX 코스피/코스닥 공통 표(ETF/ETN 제외)를 사용한다. 최신 표는 KRX에서 확인할 것.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

# (가격 미만, 호가단위). 마지막 구간(50만원 이상)은 1,000원.
_TICKS = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
]
_TOP_TICK = 1_000
LIMIT_PCT = 0.30  # 일일 가격제한폭 ±30%


def tick_size(price: float) -> int:
    for limit, tick in _TICKS:
        if price < limit:
            return tick
    return _TOP_TICK


def round_to_tick(price: float, mode: str = "nearest") -> int:
    """호가단위로 맞춘다. mode: nearest | down | up"""
    t = tick_size(price)
    q = price / t
    if mode == "down":
        n = math.floor(q + 1e-9)
    elif mode == "up":
        n = math.ceil(q - 1e-9)
    else:
        n = math.floor(q + 0.5)
    return int(n * t)


def price_limits(prev_close: float) -> tuple[int, int]:
    """전일종가 기준 하한가/상한가(호가단위 절사 반영)."""
    lower = round_to_tick(prev_close * (1 - LIMIT_PCT), "up")
    upper = round_to_tick(prev_close * (1 + LIMIT_PCT), "down")
    return lower, upper


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """최근 period일 True Range의 단순평균. df는 high/low/close 컬럼, 날짜 오름차순."""
    if len(df) < period + 1:
        raise ValueError(f"ATR 계산에는 최소 {period + 1}개 일봉이 필요합니다 (현재 {len(df)}개)")
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return float(tr.iloc[-period:].mean())


@dataclass(frozen=True)
class PricePlan:
    prev_close: int
    atr: float
    buy: Optional[int]   # 매수 의견이 아니면 None
    sell: int            # 목표가(매수 의견) 또는 매도 기준가(매도 의견)
    stop: Optional[int]  # 매도 의견이면 None


def _fit(price: float, lower: int, upper: int) -> int:
    return min(max(round_to_tick(price), lower), upper)


def plan_long(
    prev_close: float,
    atr_value: float,
    k_entry: float = 0.3,
    k_target: float = 1.5,
    k_stop: float = 1.0,
) -> PricePlan:
    lower, upper = price_limits(prev_close)
    buy = _fit(prev_close - k_entry * atr_value, lower, upper)
    target = _fit(buy + k_target * atr_value, lower, upper)
    stop = _fit(buy - k_stop * atr_value, lower, upper)
    # 호가단위 반올림 후에도 최소 1호가 간격 보장
    target = max(target, buy + tick_size(buy))
    stop = max(min(stop, buy - tick_size(buy)), 1)
    return PricePlan(int(prev_close), atr_value, buy, target, stop)


def plan_sell(prev_close: float, atr_value: float, k_bounce: float = 0.3) -> PricePlan:
    """매도(보유 시)/회피 의견: 반등 구간에서 팔 기준가만 제시한다."""
    lower, upper = price_limits(prev_close)
    sell = _fit(prev_close + k_bounce * atr_value, lower, upper)
    return PricePlan(int(prev_close), atr_value, None, sell, None)


def profit_rate(actual: Optional[float], current: Optional[float]) -> Optional[float]:
    """수익률(%) = (현재가 - 실구매가) / 실구매가 * 100. 값이 없으면 None(공란)."""
    if actual is None or current is None:
        return None
    if pd.isna(actual) or pd.isna(current) or actual <= 0:
        return None
    return (current - actual) / actual * 100
