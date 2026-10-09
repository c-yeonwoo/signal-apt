"""경매 목적별 입찰 시뮬레이션.

금액 단위는 기존 경매 API와 같은 만원이다. 세율·대출·보증금은 사건/사용자별
입력이지 정책의 자동 판정값이 아니다. 미확정 권리는 0으로 치환하지 않는다.
"""

from __future__ import annotations

from datetime import date
from math import ceil, isfinite

from realty_signal.auction import Listing
from realty_signal.time_kst import today_kst

PURPOSES = {"owner", "flip", "rent"}
ENGINE_VERSION = "auction-bid-1"


def _number(data: dict, key: str, default: float | None = None) -> float | None:
    value = data.get(key, default)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key}: 숫자를 입력하세요")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key}: 숫자를 입력하세요") from exc
    if not isfinite(result) or result < 0:
        raise ValueError(f"{key}: 0 이상의 유한한 숫자를 입력하세요")
    return result


def _round(value: float | None) -> int | None:
    return round(value) if value is not None else None


def _comparable_market(lst: Listing, data: dict) -> tuple[float | None, list[str], bool]:
    """동일 전용면적(±0.1㎡)의 최근 6개월 실거래 3건 이상만 검증 시세로 사용."""
    comps = data.get("comparables") or []
    if not isinstance(comps, list):
        raise ValueError("comparables: 목록이어야 합니다")
    amounts = []
    seen = set()
    today = today_kst()
    stamp = None
    try:
        stamp = date.fromisoformat(lst.실거래표본갱신일)
    except (TypeError, ValueError):
        pass
    trusted_cache = stamp is not None and 0 <= (today - stamp).days <= 7
    for item in lst.실거래표본 if trusted_cache else []:
        if not isinstance(item, dict) or item.get("source") != "molit":
            continue
        try:
            amount = _number(item, "price")
            area = _number(item, "exclusive_m2")
            sold_at = date.fromisoformat(str(item.get("sold_at", ""))[:10])
        except (ValueError, TypeError):
            continue
        months = (today.year - sold_at.year) * 12 + today.month - sold_at.month
        if (amount and area and lst.전용면적 > 0 and abs(area - lst.전용면적) <= 0.1
                and 0 <= months <= 6 and sold_at <= today):
            identity = (sold_at, amount, str(item.get("floor"))[:10])
            if identity in seen:
                continue
            seen.add(identity)
            amounts.append(amount)
    manual = _number(data, "market_low")
    if len(amounts) >= 3:
        # 저가 실거래를 보수적 기준으로 쓴다. 입찰 경쟁 표본과 섞지 않는다.
        low = min(amounts)
        return min(low, manual) if manual else low, [
            f"국토부 동일 면적 최근 실거래 {len(amounts)}건 · 최저가 기준"], True
    if manual:
        return manual, ["시세는 사용자 가정입니다. 동일 면적 최근 실거래 3건 확인 전입니다."], False
    if comps:
        return None, ["사용자 입력 거래는 공식 수집 표본으로 확인되지 않았습니다."], False
    return None, ["동일 면적 최근 실거래 3건 또는 보수적 시세 입력이 필요합니다."], False


def evaluate(lst: Listing, data: dict) -> dict:
    """한 입찰가와 목적별 상한을 계산한다. 검증 전 상한은 가정 시뮬레이션으로만 반환."""
    purpose = data.get("purpose", "owner")
    if purpose not in PURPOSES:
        raise ValueError("purpose: owner, flip, rent 중 선택하세요")
    floor = lst.최저매각가 if lst.최저매각가 and lst.최저매각가 > 0 else None
    market, market_notes, market_verified = _comparable_market(lst, data)
    bid = _number(data, "bid", floor)
    tax_rate = _number(data, "tax_rate")
    buy_broker_rate = _number(data, "buy_broker_rate")
    loan = _number(data, "confirmed_loan", 0)
    loan_rate = _number(data, "loan_rate", 0)
    hold_months = _number(data, "hold_months", 0)
    legal = _number(data, "legal_cost", 0)
    eviction = _number(data, "eviction_cost")
    management = _number(data, "management_cost", lst.미납관리비)
    repair = _number(data, "repair_cost", lst.수리비)
    contingency = _number(data, "contingency", 0)
    court_deposit = _number(data, "court_deposit")
    cash_budget = _number(data, "cash_budget")
    if court_deposit is not None and bid is not None and court_deposit > bid:
        raise ValueError("court_deposit: 입찰가를 넘을 수 없습니다")
    if loan is not None and bid is not None and loan > bid:
        raise ValueError("confirmed_loan: 입찰가를 넘을 수 없습니다")
    required = []
    if floor is None:
        required.append("법원 공고의 이번 회차 최저매각가")
    if lst.전용면적 <= 0:
        required.append("전용면적 확인")
    if market is None:
        required.append("동일 면적 보수적 시세")
    rights = lst.권리분석 or {}
    analysis = rights.get("분석") or {}
    stored_rights_ready = (lst.인수보증금 is not None and rights.get("조사완료") is True
                           and analysis.get("확인필요") is False
                           and analysis.get("인수합계") is not None
                           and lst.인수보증금 == analysis.get("인수합계"))
    # 자동 수집 목록에는 권리 원문이 없다. 사용자가 넣은 금액은 저장된 분석이나
    # 법원 확인으로 승격하지 않고 끝까지 가정 시뮬레이션으로만 취급한다.
    assumed_rights = _number(data, "assumed_inherited_cost")
    use_rights_assumption = assumed_rights is not None and data.get("rights_reviewed") is True
    inherited = assumed_rights if use_rights_assumption else None
    if inherited is None and stored_rights_ready:
        inherited = lst.인수보증금
    if inherited is None:
        required.append("권리·점유·인수보증금 확인")
    if tax_rate is None or tax_rate > 1:
        required.append("개인 조건에 맞는 취득 관련 세율")
    if eviction is None:
        required.append("명도비 가정")
    if court_deposit is None or court_deposit <= 0:
        required.append("법원 공고의 입찰보증금")
    if cash_budget is None:
        required.append("사용 가능한 현금 한도")
    if loan and not loan_rate:
        required.append("확인한 대출 금리")
    if bid is None or bid <= 0 or floor is not None and bid < floor:
        required.append("최저매각가 이상의 시뮬레이션 입찰가")
    if purpose == "owner":
        min_saving = _number(data, "min_saving")
        if min_saving is None:
            required.append("목표 절약액")
        if buy_broker_rate is None:
            required.append("일반매매 중개율")
    elif purpose == "flip":
        sale_low = _number(data, "sale_low")
        sale_broker_rate = _number(data, "sale_broker_rate")
        sale_tax = _number(data, "sale_tax")
        min_profit = _number(data, "min_profit")
        if None in (sale_low, sale_broker_rate, sale_tax, min_profit):
            required.append("보수적 매도가·매도 비용·세금·목표 순이익")
    else:
        rent_monthly = _number(data, "rent_monthly")
        rent_deposit = _number(data, "rent_deposit")
        vacancy_rate = _number(data, "vacancy_rate")
        annual_operating = _number(data, "annual_operating")
        annual_principal = _number(data, "annual_principal", 0)
        target_yield = _number(data, "target_yield")
        if None in (rent_monthly, rent_deposit, vacancy_rate, annual_operating, target_yield):
            required.append("임대료·임대보증금·공실·운영비·목표 현금수익률")
        if vacancy_rate is not None and vacancy_rate > 1 or target_yield is not None and target_yield > 1:
            required.append("공실률과 목표 수익률은 0~1 범위")
    if purpose != "owner" and "hold_months" not in data:
        required.append("예상 보유기간")
    if buy_broker_rate is not None and buy_broker_rate > 1:
        required.append("일반매매 중개율은 0~1 범위")

    def calc(price: float) -> dict:
        # 승인액을 낙찰가·잔금보다 많이 적용하지 않는다. 보증금은 매각대금에 충당된다.
        credit = min(loan or 0, max(0, price - (court_deposit or 0)))
        tax = price * (tax_rate or 0)
        carry = credit * (loan_rate or 0) * (hold_months or 0) / 12
        known_cost = (tax + (legal or 0) + (eviction or 0) + (management or 0)
                      + (repair or 0) + (contingency or 0) + inherited)
        total = price + known_cost + carry
        # 보증금→잔금→명도/수리 순서. 미래 임대보증금은 잔금 자금에 선반영하지 않는다.
        peak_cash = max(court_deposit or 0,
                        price - credit + tax + (legal or 0) + inherited,
                        total - credit)
        result = {"입찰가": _round(price), "취득세가정": _round(tax),
                  "확인대출반영": _round(credit), "보유이자": _round(carry),
                  "경매총비용": _round(total), "최대필요현금": _round(peak_cash),
                  "인수보증금": _round(inherited),
                  "현금단계": [
                      {"단계": "입찰", "유출": _round(court_deposit)},
                      {"단계": "잔금·취득", "유출": _round(
                          price - (court_deposit or 0) - credit + tax + (legal or 0)
                          + inherited)},
                      {"단계": "명도·수리", "유출": _round(
                          (eviction or 0) + (management or 0) + (repair or 0)
                          + (contingency or 0))},
                      {"단계": "보유", "유출": _round(carry)},
                  ]}
        if purpose == "owner":
            direct = market * (1 + (tax_rate or 0) + (buy_broker_rate or 0)) + (legal or 0)
            saving = direct - total
            result.update({"일반매매가정비용": _round(direct), "절약액": _round(saving),
                           "목표충족": saving >= min_saving})
        elif purpose == "flip":
            net_sale = sale_low * (1 - sale_broker_rate) - sale_tax
            profit = net_sale - total
            result.update({"세후순이익가정": _round(profit),
                           "현금수익률": round(profit / peak_cash * 100, 1) if peak_cash > 0 else None,
                           "목표충족": profit >= min_profit})
        else:
            annual_net = rent_monthly * 12 * (1 - vacancy_rate) - annual_operating \
                - credit * (loan_rate or 0) - annual_principal
            # 보증금은 임대 개시 후 들어오는 상환 의무. 최대필요현금은 줄이지 않는다.
            after_deposit = total - credit - rent_deposit
            cash_yield = annual_net / after_deposit if after_deposit > 0 else None
            result.update({"연간순현금흐름": _round(annual_net),
                           "임대후순투입현금": _round(after_deposit),
                           "현금수익률": round(cash_yield * 100, 1) if cash_yield is not None else None,
                           "목표충족": cash_yield is not None and annual_net >= 0
                           and cash_yield >= target_yield})
            result["현금단계"].append({"단계": "임대 개시 후", "유입": _round(rent_deposit),
                                    "반환의무": _round(rent_deposit)})
        result["자금충족"] = peak_cash <= cash_budget
        return result

    if required:
        return {"version": ENGINE_VERSION, "status": "needs_review", "purpose": purpose,
                "scenario": None, "scenario_ceiling": None, "review_ceiling": None,
                "missing": required, "market_notes": market_notes}
    if purpose == "flip" and sale_broker_rate > 1:
        raise ValueError("sale_broker_rate: 0~1 범위여야 합니다")
    scenario = calc(bid)
    # 비용/현금은 입찰가에 대해 단조 증가한다. 단위 만원 이분 탐색으로 1% 격자 누락을 막는다.
    lo, hi = max(ceil(floor), ceil(court_deposit)), int(market)
    if purpose == "rent" and lo <= hi:
        # 임대보증금이 초기 자본을 모두 덮는 구간은 수익률 분모가 0 이하라 제외한다.
        # 그 구간 뒤부터 현금수익률은 가격 상승에 따라 감소한다.
        start, end, first_positive = lo, hi, None
        while start <= end:
            mid = (start + end) // 2
            if calc(mid)["임대후순투입현금"] > 0:
                first_positive = mid
                end = mid - 1
            else:
                start = mid + 1
        lo = first_positive if first_positive is not None else hi + 1
    ceiling = None
    while lo <= hi:
        mid = (lo + hi) // 2
        row = calc(mid)
        if row["목표충족"] and row["자금충족"]:
            ceiling = mid
            lo = mid + 1
        else:
            hi = mid - 1
    # 사용자가 입력한 근거는 법원·세무·은행의 확인 결과와 같지 않다.
    pending = []
    if data.get("court_documents_checked") is not True:
        pending.append("법원 서류 직접 확인")
    if data.get("tax_checked") is not True:
        pending.append("취득·매도 세금 확인")
    if data.get("costs_checked") is not True:
        pending.append("점유·명도·관리·수리 비용 확인")
    if loan and data.get("loan_checked") is not True:
        pending.append("금융기관 대출 실행액·금리 확인")
    if not market_verified:
        pending.append("동일 면적 국토부 실거래 3건 갱신")
    if use_rights_assumption:
        pending.append("인수금액은 사용자 가정 · 법원 원본과 권리 재확인")
    if contingency <= 0:
        pending.append("예비비 입력")
    checked = not pending
    return {"version": ENGINE_VERSION,
            "status": "conditional_ceiling" if ceiling is not None and checked else
                      "assumption_only" if ceiling is not None else "no_bid",
            "purpose": purpose, "scenario": scenario, "scenario_ceiling": ceiling,
            "review_ceiling": ceiling if checked else None,
            "missing": pending,
            "market_notes": market_notes}
