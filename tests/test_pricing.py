import random

import pandas as pd
import pytest

from engine import pricing


@pytest.mark.parametrize(
    "price,tick",
    [(1_999, 1), (2_000, 5), (4_995, 5), (5_000, 10), (19_990, 10), (20_000, 50),
     (49_950, 50), (50_000, 100), (199_900, 100), (200_000, 500), (499_500, 500), (500_000, 1_000)],
)
def test_tick_size_boundaries(price, tick):
    assert pricing.tick_size(price) == tick


def test_round_modes():
    assert pricing.round_to_tick(50_049, "nearest") == 50_000
    assert pricing.round_to_tick(50_051, "nearest") == 50_100
    assert pricing.round_to_tick(50_099, "down") == 50_000
    assert pricing.round_to_tick(50_001, "up") == 50_100


def test_worked_example_from_plan():
    """전일종가 50,000 / ATR 1,500 → 49,550 / 51,800 / 48,050"""
    p = pricing.plan_long(50_000, 1_500)
    assert (p.buy, p.sell, p.stop) == (49_550, 51_800, 48_050)


def test_atr_simple_mean():
    # 14일 동안 매일 고저폭 100, 갭 없음 → ATR = 100
    n = 15
    df = pd.DataFrame({"high": [1_100] * n, "low": [1_000] * n, "close": [1_050] * n})
    assert pricing.atr(df, 14) == pytest.approx(100.0)


def test_atr_uses_gap():
    df = pd.DataFrame(
        {"high": [100, 100], "low": [90, 90], "close": [95, 95]}
    )
    df.loc[1, ["high", "low", "close"]] = [130, 125, 128]  # 전일종가 95 → 갭 상승, TR = |130-95| = 35
    assert pricing.atr(df, 1) == pytest.approx(35.0)


def test_atr_needs_enough_rows():
    with pytest.raises(ValueError):
        pricing.atr(pd.DataFrame({"high": [1] * 5, "low": [1] * 5, "close": [1] * 5}), 14)


def test_limits():
    lo, hi = pricing.price_limits(10_000)
    assert (lo, hi) == (7_000, 13_000)


def test_prices_are_on_tick_inside_limits_and_ordered():
    rng = random.Random(7)
    for _ in range(500):
        prev = rng.choice([1_500, 3_300, 8_700, 24_500, 72_000, 195_000, 640_000, 980_000])
        atr_value = prev * rng.uniform(0.005, 0.12)
        p = pricing.plan_long(prev, atr_value)
        lo, hi = pricing.price_limits(prev)
        for price in (p.buy, p.sell, p.stop):
            assert price % pricing.tick_size(price) == 0
            assert 1 <= price <= hi
        assert p.buy >= lo and p.sell >= lo
        assert p.stop < p.buy < p.sell


def test_plan_sell_has_no_buy_or_stop():
    p = pricing.plan_sell(100_000, 2_000)
    assert p.buy is None and p.stop is None and p.sell > 100_000


def test_profit_rate_blank_when_missing():
    assert pricing.profit_rate(None, 100) is None
    assert pricing.profit_rate(100, None) is None
    assert pricing.profit_rate(float("nan"), 100) is None
    assert pricing.profit_rate(0, 100) is None
    assert pricing.profit_rate(100, 110) == pytest.approx(10.0)
    assert pricing.profit_rate(200, 150) == pytest.approx(-25.0)
