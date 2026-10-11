"""종목 선정 로직: 뉴스 점수 → 시장 국면 → 안전 필터 → 종합 점수 → 가격 산출.

모든 가중치·임계값은 검증되지 않은 출발점(가설)이다. 6단계(백테스트)에서 조정할 것.

종합 점수(0~100) = 뉴스 40% + 거래량 20% + 변동성(ATR) 20% + 시장국면 적합도 20%
  - 뉴스: 방향(+/-)은 의견을 결정하고, 크기(|점수|)만 점수에 반영
  - 거래량: 직전 거래일 거래량 / 직전 20일 평균 (3배면 100점)
  - 변동성: ATR / 종가 (5%면 100점)
  - 국면 적합도: 매수는 공격장에서, 매도/회피는 방어장에서 높게
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from . import pricing
from .data import DATA_DIR, load_universe

WEIGHTS = {"news": 0.4, "volume": 0.2, "atr": 0.2, "regime": 0.2}
NEWS_SCALE = 4.0           # 원점수 4점 = 100점
SIGNAL_THRESHOLD = 25.0    # |뉴스점수| 이 값 미만이면 추천하지 않음
MIN_TRADING_VALUE = 5e9    # 20일 평균 거래대금 하한(원)
REGIME_PARAMS = {
    "공격": {"max_picks": 5, "k_stop": 1.0},
    "방어": {"max_picks": 3, "k_stop": 0.7},  # 방어장: 추천 수 축소 + 손절 타이트
}


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def load_keywords(path: Path | None = None) -> list[dict]:
    df = pd.read_csv(path or DATA_DIR / "keywords.csv")
    return df.to_dict("records")


def load_blocklist(path: Path | None = None) -> set[str]:
    p = path or DATA_DIR / "blocklist.csv"
    if not Path(p).exists():
        return set()
    df = pd.read_csv(p, dtype={"code": str})
    return set(df["code"].dropna())


def parse_headlines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def score_news(headlines: Iterable[str], keywords: list[dict]) -> dict[str, dict]:
    """섹터별 원점수와 근거. 같은 키워드가 여러 기사에 나오면 보도 횟수만큼 누적된다."""
    out: dict[str, dict] = {}
    for headline in headlines:
        for kw in keywords:
            if kw["keyword"] in headline:
                entry = out.setdefault(kw["sector"], {"raw": 0.0, "hits": []})
                entry["raw"] += float(kw["polarity"]) * float(kw["weight"])
                entry["hits"].append((kw["keyword"], headline))
    return out


def news_score(raw: float) -> float:
    """원점수 → -100~+100"""
    return _clamp(raw / NEWS_SCALE * 100, -100.0, 100.0)


def judge_regime(index_df: pd.DataFrame, ma_window: int = 20) -> dict:
    """지수 종가가 20일 이동평균 위면 '공격', 아니면 '방어'. (환율·선물 반영은 추후 과제)"""
    close = index_df["close"]
    ma = float(close.rolling(ma_window).mean().iloc[-1])
    last = float(close.iloc[-1])
    return {
        "mode": "공격" if last > ma else "방어",
        "last": round(last, 2),
        "ma": round(ma, 2),
        "gap_pct": round((last / ma - 1) * 100, 2),
    }


def _opinion(news: float) -> Optional[str]:
    if news >= SIGNAL_THRESHOLD:
        return "매수"
    if news <= -SIGNAL_THRESHOLD:
        return "매도/회피"
    return None


def _regime_fit(opinion: str, mode: str) -> float:
    if opinion == "매수":
        return 100.0 if mode == "공격" else 30.0
    return 100.0 if mode == "방어" else 60.0


def build_recommendations(
    headlines: list[str],
    provider,
    run_date: date,
    universe: pd.DataFrame | None = None,
    keywords: list[dict] | None = None,
    blocklist: set[str] | None = None,
    regime_override: str | None = None,
    min_trading_value: float = MIN_TRADING_VALUE,
) -> tuple[list[dict], dict]:
    universe = universe if universe is not None else load_universe()
    keywords = keywords if keywords is not None else load_keywords()
    blocklist = blocklist if blocklist is not None else load_blocklist()

    regime = judge_regime(provider.index_history(60))
    if regime_override in REGIME_PARAMS:
        regime["mode"] = regime_override
        regime["overridden"] = True
    mode = regime["mode"]
    params = REGIME_PARAMS[mode]

    sector_news = score_news(headlines, keywords)
    meta = {"regime": regime, "excluded": [], "matched_sectors": sorted(sector_news)}
    candidates: list[dict] = []

    for _, stock in universe.iterrows():
        code, name, sector = stock["code"], stock["name"], stock["sector"]
        if sector not in sector_news:
            continue
        ns = news_score(sector_news[sector]["raw"])
        opinion = _opinion(ns)
        if opinion is None:
            continue
        if code in blocklist:
            meta["excluded"].append((code, name, "제외 목록(관리/경고 종목)"))
            continue

        hist = provider.history(code, 60)
        if len(hist) < 22:
            meta["excluded"].append((code, name, "시세 데이터 부족"))
            continue
        avg_tv = float((hist["close"] * hist["volume"]).tail(20).mean())
        if avg_tv < min_trading_value:
            meta["excluded"].append((code, name, f"거래대금 부족({avg_tv / 1e8:,.0f}억)"))
            continue

        prev_close = float(hist["close"].iloc[-1])
        atr_value = pricing.atr(hist, 14)
        vol_ratio = float(hist["volume"].iloc[-1] / hist["volume"].iloc[-21:-1].mean())
        volume_score = _clamp((vol_ratio - 1) / 2.0 * 100)
        atr_score = _clamp(atr_value / prev_close / 0.05 * 100)
        fit = _regime_fit(opinion, mode)
        total = (
            WEIGHTS["news"] * abs(ns)
            + WEIGHTS["volume"] * volume_score
            + WEIGHTS["atr"] * atr_score
            + WEIGHTS["regime"] * fit
        )

        if opinion == "매수":
            plan = pricing.plan_long(prev_close, atr_value, k_stop=params["k_stop"])
        else:
            plan = pricing.plan_sell(prev_close, atr_value)

        hits = sector_news[sector]["hits"]
        kws = list(dict.fromkeys(k for k, _ in hits))[:3]
        reason = f"{sector} 뉴스 {ns:+.0f}점 ({', '.join(kws)})"

        candidates.append(
            {
                "run_date": run_date.isoformat(),
                "code": code,
                "name": name,
                "sector": sector,
                "opinion": opinion,
                "score": round(total, 1),
                "news_score": round(ns, 1),
                "buy_price": plan.buy,
                "sell_price": plan.sell,
                "stop_price": plan.stop,
                "prev_close": plan.prev_close,
                "reason": reason,
            }
        )

    candidates.sort(key=lambda r: r["score"], reverse=True)
    return candidates[: params["max_picks"]], meta
