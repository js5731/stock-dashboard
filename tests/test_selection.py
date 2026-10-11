from datetime import date

import pandas as pd
import pytest

from engine import selection
from engine.data import SampleProvider, load_universe

KEYWORDS = [
    {"keyword": "AI 반도체", "sector": "반도체", "polarity": 1, "weight": 2.0},
    {"keyword": "유가 급락", "sector": "정유", "polarity": -1, "weight": 2.0},
    {"keyword": "유가 급락", "sector": "항공", "polarity": 1, "weight": 1.5},
]


def test_parse_headlines_skips_comments_and_blanks():
    assert selection.parse_headlines("# 주석\n\n기사 A\n  기사 B  \n") == ["기사 A", "기사 B"]


def test_score_news_sign_and_accumulation():
    out = selection.score_news(["AI 반도체 호재", "또 AI 반도체 호재", "유가 급락"], KEYWORDS)
    assert out["반도체"]["raw"] == pytest.approx(4.0)  # 두 기사 누적
    assert out["정유"]["raw"] < 0 < out["항공"]["raw"]  # 같은 뉴스, 섹터별 반대 방향


def test_news_score_clamped():
    assert selection.news_score(100) == 100
    assert selection.news_score(-100) == -100
    assert selection.news_score(2) == pytest.approx(50)


def test_regime_attack_vs_defense():
    up = pd.DataFrame({"close": list(range(100, 140))})
    down = pd.DataFrame({"close": list(range(140, 100, -1))})
    assert selection.judge_regime(up)["mode"] == "공격"
    assert selection.judge_regime(down)["mode"] == "방어"


def _build(headlines, **kw):
    provider = SampleProvider(end=date(2026, 10, 9))
    return selection.build_recommendations(headlines, provider, date(2026, 10, 9), **kw)


def test_no_matching_news_gives_no_recommendations():
    recs, _ = _build(["오늘 날씨는 맑음"])
    assert recs == []


def test_opinions_and_price_relations():
    recs, meta = _build(
        ["AI 반도체 수요 기대, 엔비디아 호실적, HBM 부족", "전기차 보조금 축소 확정, 전기차 수요 둔화"],
        regime_override="공격",
    )
    assert recs, "신호가 강한 뉴스인데 추천이 비어 있음"
    assert len(recs) <= selection.REGIME_PARAMS["공격"]["max_picks"]
    scores = [r["score"] for r in recs]
    assert scores == sorted(scores, reverse=True)
    for r in recs:
        assert 0 <= r["score"] <= 100
        if r["opinion"] == "매수":
            assert r["news_score"] >= selection.SIGNAL_THRESHOLD
            assert r["stop_price"] < r["buy_price"] < r["sell_price"]
        else:
            assert r["opinion"] == "매도/회피" and r["news_score"] <= -selection.SIGNAL_THRESHOLD
            assert r["buy_price"] is None and r["stop_price"] is None


def test_defense_mode_reduces_picks_and_tightens_stop():
    news = ["AI 반도체 수요 기대, 엔비디아 호실적, HBM 부족", "전기차 보조금 축소 확정, 전기차 수요 둔화"]
    attack, _ = _build(news, regime_override="공격")
    defense, _ = _build(news, regime_override="방어")
    assert len(defense) <= selection.REGIME_PARAMS["방어"]["max_picks"] <= len(attack) or len(defense) <= 3
    a = {r["code"]: r for r in attack if r["opinion"] == "매수"}
    d = {r["code"]: r for r in defense if r["opinion"] == "매수"}
    for code in set(a) & set(d):
        assert (d[code]["buy_price"] - d[code]["stop_price"]) <= (a[code]["buy_price"] - a[code]["stop_price"])


def test_illiquid_stock_is_excluded():
    recs, meta = _build(["AI 반도체 수요 기대, 엔비디아 호실적, HBM 부족"], min_trading_value=1e18)
    assert recs == []
    assert any("거래대금" in why for _, _, why in meta["excluded"])


def test_blocklist_is_excluded():
    universe = load_universe()
    codes = set(universe.loc[universe["sector"] == "반도체", "code"])
    recs, meta = _build(["AI 반도체 수요 기대, 엔비디아 호실적, HBM 부족"], blocklist=codes)
    assert recs == []
    assert len(meta["excluded"]) == len(codes)
