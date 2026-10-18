"""추천 종목 수동 관리(Object 메뉴)용 입력 검증. Streamlit에 의존하지 않는다."""
from __future__ import annotations

from . import pricing

OPINIONS = ("매수", "매도/회피")


def validate_recommendation(rec: dict) -> list[str]:
    """가격 불변식(CLAUDE.md 규칙 6)을 검사해 오류 메시지 목록을 돌려준다. 비어 있으면 통과."""
    errors: list[str] = []
    code = (rec.get("code") or "").strip()
    if not (len(code) == 6 and code.isalnum()):
        errors.append("종목코드는 6자리여야 합니다.")
    if not (rec.get("name") or "").strip():
        errors.append("종목명은 필수입니다.")
    opinion = rec.get("opinion")
    if opinion not in OPINIONS:
        errors.append(f"의견은 {OPINIONS} 중 하나여야 합니다.")

    prev = rec.get("prev_close")
    buy, sell, stop = rec.get("buy_price"), rec.get("sell_price"), rec.get("stop_price")
    if not prev or prev <= 0:
        errors.append("전일종가는 필수이며 0보다 커야 합니다.")
        return errors
    if sell is None:
        errors.append("매도가(목표가)는 필수입니다.")

    lower, upper = pricing.price_limits(prev)
    for label, price in (("매수가", buy), ("매도가", sell), ("손절가", stop)):
        if price is None:
            continue
        if price != pricing.round_to_tick(price):
            errors.append(f"{label} {price:,.0f}원은 호가단위 배수가 아닙니다.")
        if not lower <= price <= upper:
            errors.append(f"{label} {price:,.0f}원이 상·하한가({lower:,}~{upper:,}) 밖입니다.")

    if opinion == "매수":
        if buy is None or stop is None or sell is None:
            errors.append("매수 의견은 매수가·매도가·손절가가 모두 필요합니다.")
        elif not stop < buy < sell:
            errors.append("매수 의견은 손절가 < 매수가 < 목표가여야 합니다.")
    elif opinion == "매도/회피" and (buy is not None or stop is not None):
        errors.append("매도/회피 의견은 매수가·손절가를 비워야 합니다.")
    return errors


def suggest_from_code(code: str, provider, universe, quote=None) -> dict:
    """종목코드로 폼 초기값을 만든다: 종목명·섹터(quote.stock_info API 우선, 없거나 실패하면 universe) + 시세 기반 전일종가·매수/매도/손절 제안.
    찾지 못한 값은 키를 생략한다. 제안값은 검증 전 가설 계수(plan_long)로 계산한 참고값이다."""
    code = code.strip()
    out: dict = {}
    hit = universe[universe["code"] == code]
    if len(hit):
        out["name"], out["sector"] = hit["name"].iloc[0], hit["sector"].iloc[0]
    if quote is not None:
        try:
            out.update(quote.stock_info(code))  # API 값이 있는 항목만 universe 값을 덮어쓴다
        except Exception:
            pass
    # 전일종가: 키움 기준가 → (없으면) 키움 현재가 → (없으면) 시세 제공자의 최근 종가
    prev, source = None, None
    if quote is not None:
        for attr, label in (("prev_close", "전일종가"), ("latest_price", "현재가")):
            try:
                prev = getattr(quote, attr)(code)
            except Exception:
                prev = None
            if prev:
                source = label
                break
    try:
        hist = provider.history(code, 30)
        last = float(hist["close"].iloc[-1])
        atr_value = pricing.atr(hist)
    except Exception:
        hist = None
    if hist is None:
        if prev:  # 변동성(ATR)을 알 수 없어 가격 제안은 하지 않는다
            out.update(prev_close=int(prev), prev_source=source)
        return out
    if not prev:
        prev, source = last, "시세 제공자"
    atr_value *= prev / last  # 제공자 종가와 기준 가격이 다르면 ATR을 같은 비율로 맞춘다
    plan = pricing.plan_long(prev, atr_value)
    out.update(
        prev_close=int(prev), prev_source=source, opinion="매수",
        buy_price=plan.buy, sell_price=plan.sell, stop_price=plan.stop,
    )
    return out
