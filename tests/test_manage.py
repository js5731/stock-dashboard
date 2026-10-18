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


class _FakeProvider:
    def history(self, code, days):
        import pandas as pd
        n = 30
        return pd.DataFrame({"high": [51_000] * n, "low": [49_000] * n, "close": [50_000] * n})


class _FakeQuote:
    def __init__(self, info=None, boom=False, prev=None, cur=None):
        self.info, self.boom, self.prev, self.cur = info or {}, boom, prev, cur

    def prev_close(self, code):
        return self.prev

    def latest_price(self, code):
        return self.cur

    def stock_info(self, code):
        if self.boom:
            raise RuntimeError("api down")
        return self.info


def _uni():
    import pandas as pd
    return pd.DataFrame({"code": ["005930"], "name": ["삼성전자(csv)"], "sector": ["반도체"]})


def test_suggest_prefers_api_name_and_sector():
    from engine.manage import suggest_from_code
    r = suggest_from_code("005930", _FakeProvider(), _uni(), _FakeQuote({"name": "삼성전자", "sector": "전기전자"}))
    assert (r["name"], r["sector"]) == ("삼성전자", "전기전자")
    assert r["stop_price"] < r["buy_price"] < r["sell_price"]


def test_suggest_falls_back_to_universe_when_api_empty_or_fails():
    from engine.manage import suggest_from_code
    for q in (_FakeQuote({}), _FakeQuote(boom=True), None):
        r = suggest_from_code("005930", _FakeProvider(), _uni(), q)
        assert (r["name"], r["sector"]) == ("삼성전자(csv)", "반도체")


def test_prev_close_from_api_beats_provider():
    from engine.manage import suggest_from_code
    r = suggest_from_code("005930", _FakeProvider(), _uni(), _FakeQuote(prev=100_000))
    assert r["prev_close"] == 100_000 and r["prev_source"] == "전일종가"
    assert r["stop_price"] < r["buy_price"] < r["sell_price"] <= 130_000  # ATR을 가격 비율에 맞춰 조정


def test_current_price_used_when_prev_close_missing():
    from engine.manage import suggest_from_code
    r = suggest_from_code("005930", _FakeProvider(), _uni(), _FakeQuote(prev=None, cur=80_000))
    assert r["prev_close"] == 80_000 and r["prev_source"] == "현재가"


def test_provider_close_used_when_api_has_nothing():
    from engine.manage import suggest_from_code
    r = suggest_from_code("005930", _FakeProvider(), _uni(), _FakeQuote())
    assert r["prev_close"] == 50_000 and r["prev_source"] == "시세 제공자"


def test_api_prev_close_without_history_skips_price_plan():
    from engine.manage import suggest_from_code

    class NoHist:
        def history(self, code, days):
            raise RuntimeError("no data")

    r = suggest_from_code("005930", NoHist(), _uni(), _FakeQuote(prev=70_000))
    assert r["prev_close"] == 70_000 and "buy_price" not in r


def test_msg_when_no_api_keys():
    from engine.manage import suggest_from_code
    r = suggest_from_code("005930", _FakeProvider(), _uni(), None)
    assert any("API 키" in m for m in r["api_msgs"])


def test_msgs_explain_api_failure_with_reason():
    from engine.manage import suggest_from_code

    q = _FakeQuote()
    q.last_error = "ka10001: 권한 없음"
    r = suggest_from_code("999999", _FakeProvider(), _uni(), q)  # CSV에도 없는 종목
    text = " ".join(r["api_msgs"])
    assert "종목명을 조회하지 못했습니다" in text and "직접 입력" in text
    assert "전일종가를 조회하지 못했습니다" in text and "현재가를 조회하지 못했습니다" in text
    assert "권한 없음" in text  # 실패 사유 표시
    assert "시세 제공자" in text


def test_no_msgs_when_api_fully_succeeds():
    from engine.manage import suggest_from_code
    r = suggest_from_code("005930", _FakeProvider(), _uni(), _FakeQuote({"name": "삼성전자", "sector": "전기전자"}, prev=70_000))
    assert r["api_msgs"] == []


def test_current_price_fallback_message():
    from engine.manage import suggest_from_code
    r = suggest_from_code("005930", _FakeProvider(), _uni(), _FakeQuote({"name": "a", "sector": "b"}, cur=80_000))
    assert any("현재가를 전일종가 칸에 넣었습니다" in m for m in r["api_msgs"])
