from engine.manage import validate_recommendation

BASE = {"code": "005930", "name": "삼성전자", "opinion": "매수", "prev_close": 50_000,
        "buy_price": 49_550, "sell_price": 51_800, "stop_price": 48_050}


def test_valid_buy_passes():
    assert validate_recommendation(BASE) == []


def test_buy_order_violation():
    assert validate_recommendation({**BASE, "stop_price": 49_600})


def test_not_tick_multiple():
    assert validate_recommendation({**BASE, "buy_price": 49_551})


def test_outside_price_limit():
    assert validate_recommendation({**BASE, "sell_price": 70_000})


def test_sell_opinion_requires_blank_buy_and_stop():
    sell = {**BASE, "opinion": "매도/회피", "buy_price": None, "stop_price": None}
    assert validate_recommendation(sell) == []
    assert validate_recommendation({**sell, "buy_price": 49_550})


def test_bad_code_and_missing_name():
    assert len(validate_recommendation({**BASE, "code": "12", "name": ""})) == 2
