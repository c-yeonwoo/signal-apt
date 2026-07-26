"""매수력 확정서 — "얼마짜리 집을, 얼마 대출로, 매달 얼마 내고 살 수 있는가".

기존 `api._max_purchase`(LTV·DSR40%·취득세·중개비)를 확장한다.

- 기대출 연원리금을 DSR 한도에서 차감
- 스트레스 DSR(가산금리)로 한도 산출, 월 상환액은 실제 금리로 표시
- 주택수·규제지역·생애최초에 따른 LTV 상한 / 취득세 차등
- 중개보수 구간 요율
- 계약금·이사·수리를 포함한 초기 필요현금
- 소득 대비 월 상환 부담을 넘지 않는 '안정 매수가'

모든 임계값·요율은 DEFAULTS / 테이블 상수에 모아 둔다(하드코딩 금지).
세율은 실무 근사이며 최종 확인은 이용자 책임.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

DEFAULTS = {
    "금리": 0.04,            # 연 이자율
    "만기": 30,              # 년
    "스트레스가산": 0.015,    # 스트레스 DSR 가산금리(한도 산출용)
    "DSR한도": 0.40,         # 연소득 대비 총 원리금
    "이사비": 200,           # 만원
    "수리비": 0,             # 만원
    "안전상환비율": 0.30,     # 월소득 대비 '안정' 월 상환 상한
    "생애최초감면": 200,      # 만원 (취득세 감면 한도)
    "생애최초감면상한가": 120_000,  # 만원 = 12억 이하일 때만 감면
    "탐색상한": 5_000_000,    # 만원 = 500억 (이분탐색 상한)
}

# (주택수, 규제지역) → LTV 상한. 주택수 2 = 다주택.
LTV_CAP = {
    (0, False): 0.70, (0, True): 0.50,
    (1, False): 0.60, (1, True): 0.30,
    (2, False): 0.40, (2, True): 0.00,
}
LTV_CAP_FIRST_TIME = 0.80   # 생애최초(무주택)

# 매매 중개보수 상한 — (가격상한 만원, 요율, 금액한도 만원)
BROKER_BRACKETS = [
    (5_000, 0.006, 25),
    (20_000, 0.005, 80),
    (90_000, 0.004, None),
    (120_000, 0.005, None),
    (150_000, 0.006, None),
    (float("inf"), 0.007, None),
]

_BIG_AREA_FARM_TAX = 0.002   # 전용 85㎡ 초과 농어촌특별세
_EDU_TAX_SHARE = 0.10        # 지방교육세 = 취득세 본세의 10% (근사)


@dataclass
class Params:
    """매수력 입력. 만원 단위, 비율은 소수(0.7 = 70%)."""

    capital: float                      # 가용 자기자본
    income: float | None = None         # 연소득 (없으면 DSR 미적용)
    existing_debt_annual: float = 0.0   # 기대출 연 원리금
    homes: int = 0                      # 보유 주택수 (0 무주택 / 1 / 2+ 다주택)
    first_time: bool = False            # 생애최초 주택구입
    regulated: bool = False             # 규제(조정대상)지역
    big_area: bool = False              # 전용 85㎡ 초과
    ltv: float | None = None            # 희망 LTV. None이면 제도 상한 그대로
    rate: float = DEFAULTS["금리"]
    years: int = DEFAULTS["만기"]
    stress_bp: float = DEFAULTS["스트레스가산"]
    moving_cost: float = DEFAULTS["이사비"]
    repair_cost: float = DEFAULTS["수리비"]
    dsr_limit: float = DEFAULTS["DSR한도"]
    extras: dict = field(default_factory=dict)

    def ltv_cap(self) -> float:
        """제도 상한과 희망 LTV 중 작은 쪽."""
        homes = max(0, min(2, int(self.homes or 0)))
        cap = LTV_CAP_FIRST_TIME if (self.first_time and homes == 0) else LTV_CAP[(homes, bool(self.regulated))]
        return min(cap, self.ltv) if self.ltv is not None else cap


def broker_fee(price: float) -> float:
    """매매 중개보수(만원) — 구간 요율 상한 기준."""
    for cap, rate, limit in BROKER_BRACKETS:
        if price < cap:
            fee = price * rate
            return min(fee, limit) if limit else fee
    return price * BROKER_BRACKETS[-1][1]


def _base_tax_rate(price: float, homes: int, regulated: bool) -> float:
    """취득세 본세율. 1주택(무주택 포함)은 가격 구간 1~3%, 다주택은 중과."""
    억 = price / 10_000
    single = 0.01 if 억 <= 6 else (억 * 2 / 3 - 3) / 100 if 억 <= 9 else 0.03
    if homes <= 0:
        return single
    if homes == 1:
        return 0.08 if regulated else single
    return 0.12 if regulated else 0.08


def acq_tax(price: float, p: Params) -> float:
    """취득세 + 지방교육세 + 농특세(만원). 생애최초 감면 반영."""
    homes = max(0, min(2, int(p.homes or 0)))
    base = _base_tax_rate(price, homes, bool(p.regulated))
    edu = base * _EDU_TAX_SHARE
    farm = _BIG_AREA_FARM_TAX if p.big_area else 0.0
    tax = price * (base + edu + farm)
    if p.first_time and homes == 0 and price <= DEFAULTS["생애최초감면상한가"]:
        tax = max(0.0, tax - DEFAULTS["생애최초감면"])
    return tax


def monthly_payment(loan: float, rate: float, years: int) -> float:
    """원리금균등 월 상환액(만원)."""
    n = max(1, int(years) * 12)
    mr = rate / 12
    if mr <= 0:
        return loan / n
    return loan * mr / (1 - (1 + mr) ** -n)


def dsr_loan_cap(p: Params) -> float:
    """DSR 한도 대출액(만원). 기대출 원리금 차감 + 스트레스 가산금리 적용."""
    if not p.income or p.income <= 0:
        return float("inf")
    available = p.income * p.dsr_limit - max(0.0, p.existing_debt_annual or 0.0)
    if available <= 0:
        return 0.0
    stressed = p.rate + max(0.0, p.stress_bp)
    return available / (monthly_payment(1.0, stressed, p.years) * 12)


def cash_needed(price: float, p: Params, loan: float) -> dict:
    """그 가격을 사는 데 필요한 현금 분해(만원)."""
    tax = acq_tax(price, p)
    fee = broker_fee(price)
    return {
        "자기자본투입": max(0.0, price - loan),
        "취득세": tax,
        "중개비": fee,
        "이사비": max(0.0, p.moving_cost),
        "수리비": max(0.0, p.repair_cost),
        "합계": max(0.0, price - loan) + tax + fee + max(0.0, p.moving_cost) + max(0.0, p.repair_cost),
    }


def for_price(price: float, p: Params) -> dict:
    """특정 가격을 살 때의 대출·월상환·필요현금. 예산 초과 여부 포함."""
    cap = dsr_loan_cap(p)
    loan = min(p.ltv_cap() * price, cap)
    cash = cash_needed(price, p, loan)
    m = monthly_payment(loan, p.rate, p.years)
    out = {
        "매수가": round(price),
        "대출": round(loan),
        "실효LTV": round(loan / price, 3) if price else 0.0,
        "월상환": round(m),
        "필요현금": round(cash["합계"]),
        "부족": round(max(0.0, cash["합계"] - p.capital)),
        "가능": cash["합계"] <= p.capital + 1,
        "비용": {k: round(v) for k, v in cash.items()},
    }
    if p.income:
        annual = m * 12 + max(0.0, p.existing_debt_annual or 0.0)
        out["DSR"] = round(annual / p.income * 100, 1)
        out["월소득대비"] = round(m / (p.income / 12) * 100, 1)
    return out


def _max_price_where(p: Params, ok) -> float:
    """조건 ok(price) 를 만족하는 최대 가격(만원) — 이분탐색."""
    lo, hi = 0.0, float(DEFAULTS["탐색상한"])
    for _ in range(48):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def max_purchase(p: Params) -> tuple[float, dict]:
    """자기자본으로 살 수 있는 최대 매수가(만원) + 비용 분해."""
    cap = dsr_loan_cap(p)
    ltv = p.ltv_cap()

    def affordable(price: float) -> bool:
        loan = min(ltv * price, cap)
        return cash_needed(price, p, loan)["합계"] <= p.capital

    price = round(_max_price_where(p, affordable))
    loan = round(min(ltv * price, cap))
    cash = cash_needed(price, p, loan)
    constraint = "자본"
    if price > 0 and cap < ltv * price:
        constraint = "DSR"
    elif price > 0 and loan >= ltv * price - 1:
        constraint = "LTV" if loan > 0 else "자본"
    detail = {
        "대출": loan,
        "취득세": round(cash["취득세"]),
        "중개비": round(cash["중개비"]),
        "이사비": round(cash["이사비"]),
        "수리비": round(cash["수리비"]),
        "자기자본": round(p.capital),
        "월상환": round(monthly_payment(loan, p.rate, p.years)),
        "실효LTV": round(loan / price, 3) if price else 0.0,
        "제약": constraint,
        "DSR제약": constraint == "DSR",
    }
    return price, detail


def safe_purchase(p: Params) -> dict | None:
    """월 상환액이 월소득의 '안전상환비율' 이내인 최대 매수가. 소득 없으면 None."""
    if not p.income or p.income <= 0:
        return None
    ceiling = p.income / 12 * DEFAULTS["안전상환비율"]
    cap = dsr_loan_cap(p)
    ltv = p.ltv_cap()

    def ok(price: float) -> bool:
        loan = min(ltv * price, cap)
        if cash_needed(price, p, loan)["합계"] > p.capital:
            return False
        return monthly_payment(loan, p.rate, p.years) <= ceiling

    price = round(_max_price_where(p, ok))
    if price <= 0:
        return None
    return for_price(price, p)


def statement(p: Params) -> dict:
    """매수력 확정서 — UI·브리핑·숏리스트가 공통으로 쓰는 단일 산출물."""
    price, detail = max_purchase(p)
    safe = safe_purchase(p)
    out = {
        "최대매수가": price,
        "대출": detail["대출"],
        "실효LTV": detail["실효LTV"],
        "월상환": detail["월상환"],
        "제약": detail["제약"],
        "비용": {k: detail[k] for k in ("취득세", "중개비", "이사비", "수리비")},
        "필요현금": round(cash_needed(price, p, detail["대출"])["합계"]),
        "안정매수가": safe["매수가"] if safe else None,
        "안정월상환": safe["월상환"] if safe else None,
        "LTV상한": p.ltv_cap(),
        "가정": {
            "자기자본": round(p.capital),
            "연소득": round(p.income) if p.income else None,
            "기대출연원리금": round(p.existing_debt_annual or 0),
            "주택수": int(p.homes or 0),
            "생애최초": bool(p.first_time),
            "규제지역": bool(p.regulated),
            "희망LTV": p.ltv,
            "금리": p.rate,
            "만기": p.years,
            "스트레스가산": p.stress_bp,
            "DSR한도": p.dsr_limit,
        },
    }
    if p.income:
        annual = detail["월상환"] * 12 + max(0.0, p.existing_debt_annual or 0.0)
        out["DSR"] = round(annual / p.income * 100, 1)
        out["월소득대비"] = round(detail["월상환"] / (p.income / 12) * 100, 1)
    return out


_PYEONG_BY_AREA = {"59": 17.4, "84": 25.7, "114": 35.4}


def pyeong_of(관심평수) -> float:
    """프로필 관심평형(㎡ 리스트) → 기준 평수. 미선택이면 84㎡."""
    if isinstance(관심평수, list) and 관심평수:
        vals = [_PYEONG_BY_AREA.get(str(v)) for v in 관심평수]
        vals = [v for v in vals if v]
        if vals:
            return min(vals)
    if isinstance(관심평수, str):
        legacy = {"s": 17.4, "m": 25.7, "l": 35.4}.get(관심평수)
        if legacy:
            return legacy
        if 관심평수 in _PYEONG_BY_AREA:
            return _PYEONG_BY_AREA[관심평수]
    return _PYEONG_BY_AREA["84"]


def params_from_profile(profile: dict | None, **override) -> Params:
    """프로필 → Params.

    우선순위: 화면 입력(override) > 프로필 필드 > 마지막 확정 시 가정.
    규제지역·희망LTV·금리·만기는 프로필에 없으므로 확정 가정이 유일한 기억이다.
    """
    p = profile or {}
    conf = (p.get("매수력") or {}).get("가정") or {}

    def pick(profile_key, conf_key, default=None):
        v = p.get(profile_key)
        if v is None or v == "":
            v = conf.get(conf_key)
        return default if v is None else v

    base = {
        "capital": float(pick("가용자본", "자기자본", 0) or 0),
        "income": float(p["연소득"]) if p.get("연소득") else (
            float(conf["연소득"]) if conf.get("연소득") else None),
        "existing_debt_annual": float(pick("기대출연원리금", "기대출연원리금", 0) or 0),
        "homes": int(pick("주택수", "주택수", 0) or 0),
        "first_time": bool(pick("생애최초", "생애최초", False)),
        "regulated": bool(conf.get("규제지역")),
        "ltv": conf.get("희망LTV"),
        "rate": conf.get("금리") or DEFAULTS["금리"],
        "years": int(conf.get("만기") or DEFAULTS["만기"]),
    }
    base.update({k: v for k, v in override.items() if v is not None})
    valid = set(Params.__dataclass_fields__)
    return Params(**{k: v for k, v in base.items() if k in valid})


def as_dict(p: Params) -> dict:
    return asdict(p)
