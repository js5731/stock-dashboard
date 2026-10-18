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
