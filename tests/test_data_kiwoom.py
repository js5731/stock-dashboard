from __future__ import annotations

import pytest

from engine.data import KiwoomQuote


def _q(monkeypatch, responses):
    q = KiwoomQuote(appkey="a", secretkey="b")
    it = iter(responses)
    monkeypatch.setattr(q, "_post", lambda *a, **k: next(it))
    return q


def test_requires_keys(monkeypatch):
    monkeypatch.delenv("KIWOOM_APPKEY", raising=False)
    monkeypatch.delenv("KIWOOM_SECRETKEY", raising=False)
    with pytest.raises(RuntimeError):
        KiwoomQuote()


def test_price_sign_stripped(monkeypatch):
    q = _q(monkeypatch, [{"token": "t"}, {"return_code": 0, "cur_prc": "-70500"}])
    assert q.latest_price("005930") == 70500


def test_failure_returns_none(monkeypatch):
    q = _q(monkeypatch, [{"token": "t"}, {"return_code": 1, "return_msg": "err"}])
    assert q.latest_price("005930") is None


def test_stock_info_name_and_sector(monkeypatch):
    q = _q(monkeypatch, [{"token": "t"}, {"return_code": 0, "name": "삼성전자", "upName": "전기전자"}])
    assert q.stock_info("005930") == {"name": "삼성전자", "sector": "전기전자"}


def test_stock_info_falls_back_to_ka10001_name(monkeypatch):
    q = _q(monkeypatch, [{"token": "t"}, {"return_code": 1}, {"return_code": 0, "stk_nm": "삼성전자"}])
    assert q.stock_info("005930") == {"name": "삼성전자"}


def test_stock_info_failure_returns_empty(monkeypatch):
    q = _q(monkeypatch, [{"token": "t"}, {"return_code": 1}, {"return_code": 1}])
    assert q.stock_info("005930") == {}


def test_prev_close_uses_base_price(monkeypatch):
    q = _q(monkeypatch, [{"token": "t"}, {"return_code": 0, "base_pric": "-70000", "cur_prc": "71000"}])
    assert q.prev_close("005930") == 70000


def test_prev_close_failure_returns_none(monkeypatch):
    q = _q(monkeypatch, [{"token": "t"}, {"return_code": 1}])
    assert q.prev_close("005930") is None
