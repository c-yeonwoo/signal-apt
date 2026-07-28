"""매수력 확정서 — "얼마짜리 집을, 얼마 대출로, 매달 얼마 내고 살 수 있는가".

기존 `api._max_purchase`(LTV·DSR40%·취득세·중개비)를 확장한다.

- 기대출 연원리금을 DSR 한도에서 차감
- 스트레스 DSR(가산금리)로 한도 산출, 월 상환액은 실제 금리로 표시
- 지역(규제·수도권)·주택수·생애최초에 따른 LTV 상한 / 취득세 차등
- 수도권·규제지역 주택가격별 대출 절대한도(15억↓ 6억 / 15~25억 4억 / 25억↑ 2억)
- 방공제(소액임차 최우선변제) 차감 — 기본 on, MCI/MCG 시 off
- 일시적 2주택 취득세 중과 완화 · 전용 85㎡ 초과 농특세
- 중개보수·법무·인지세·등록면허세·국민주택채권 실부담
- 이사·수리를 포함한 초기 필요현금
- 소득 대비 월 상환 부담을 넘지 않는 '안정 매수가'

대출 규제 테이블은 `regulation` 모듈에 분리했다(기준일·근거를 한 곳에서 관리).
모든 임계값·요율은 DEFAULTS / 테이블 상수에 모아 둔다(하드코딩 금지).
세율은 실무 근사이며 최종 확인은 이용자 책임.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from realty_signal import regulation as reg

DEFAULTS = {
    "금리": 0.04,            # 연 이자율
    "만기": 30,              # 년
    "DSR한도": 0.40,         # 연소득 대비 총 원리금
    "이사비": 200,           # 만원
    "수리비": 0,             # 만원
    "안전상환비율": 0.30,     # 월소득 대비 '안정' 월 상환 상한
    "생애최초감면": 200,      # 만원 (취득세 감면 한도)
    "생애최초감면상한가": 120_000,  # 만원 = 12억 이하일 때만 감면
    "탐색상한": 5_000_000,    # 만원 = 500억 (이분탐색 상한)
    "시가표준비율": 0.70,     # 등록면허세·채권용 시가표준액 ≈ 매매가×70%
    "채권매입률": 0.026,      # 국민주택채권 매입액 ≈ 시가표준×2.6% (아파트 근사)
    "채권할인율": 0.12,       # 즉시매각 시 실부담 ≈ 액면×할인율
}

# 매매 중개보수 상한 — (가격상한 만원, 요율, 금액한도 만원)
BROKER_BRACKETS = [
    (5_000, 0.006, 25),
    (20_000, 0.005, 80),
    (90_000, 0.004, None),
    (120_000, 0.005, None),
    (150_000, 0.006, None),
    (float("inf"), 0.007, None),
]

# 법무·소유권이전 등기 대행비 근사 — (가격상한 만원, 법무비 만원)
LEGAL_BRACKETS = [
    (30_000, 50),
    (60_000, 80),
    (90_000, 100),
    (float("inf"), 120),
]

# 대출 인지세(만원) — (대출상한 만원, 세액). 1천만 미만 면제.
STAMP_BRACKETS = [
    (1_000, 0),
    (5_000, 5),
    (10_000, 10),
    (float("inf"), 15),
]

_BIG_AREA_FARM_TAX = 0.002   # 전용 85㎡ 초과 농어촌특별세
_EDU_TAX_SHARE = 0.10        # 지방교육세 = 취득세 본세의 10% (근사)
_REGISTRY_RATE = 0.002       # 등록면허세 = 시가표준액 × 0.2%


@dataclass
class Params:
    """매수력 입력. 만원 단위, 비율은 소수(0.7 = 70%)."""

    capital: float                      # 가용 자기자본
    income: float | None = None         # 연소득 (없으면 DSR 미적용)
    existing_debt_annual: float = 0.0   # 기대출 연 원리금
    homes: int = 0                      # 보유 주택수 (0 무주택 / 1 / 2+ 다주택)
    first_time: bool = False            # 생애최초 주택구입
    region: str | None = None           # 매수 예정 지역 — 있으면 규제·수도권을 자동 판정
    regulated: bool = False             # 규제(조정대상·투기과열)지역
    metro: bool = False                 # 수도권 — 절대한도·스트레스 3.0%p 적용 범위
    dispose: bool = True                # 1주택자의 6개월 내 처분·전입 약정 여부
    temp_two_home: bool = False         # 일시적 2주택 — 취득세 중과 완화(1주택 세율)
    big_area: bool = False              # 전용 85㎡ 초과
    apply_bangongje: bool = True        # 방공제 차감(기본 on). MCI/MCG면 False
    ltv: float | None = None            # 희망 LTV. None이면 제도 상한 그대로
    rate: float = DEFAULTS["금리"]
    years: int = DEFAULTS["만기"]
    rate_type: str = reg.DEFAULT_RATE_TYPE   # 변동·혼합·주기·고정 (스트레스 적용비율)
    stress_bp: float | None = None      # 직접 지정 시 규제 기준 대신 이 값을 쓴다
    moving_cost: float = DEFAULTS["이사비"]
    repair_cost: float = DEFAULTS["수리비"]
    dsr_limit: float = DEFAULTS["DSR한도"]
    sido: str | None = None             # 지역명이 애매할 때(중구·강서구) 시도 힌트
    extras: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.region:
            c = reg.classify(self.region, self.sido)
            self.regulated = c["규제지역"]
            self.metro = c["수도권"]
        else:
            # 지역 미지정 = 수도권 규제지역 가정. 낙관적 LTV 80%로 예산을 부풀리지 않는다.
            self.regulated = True
            self.metro = True

    def tier(self, price: float = float("inf")) -> str:
        """차주 자격 구분 — 서민·실수요자 우대는 가격·소득에 걸린다."""
        return reg.tier(self.homes, first_time=self.first_time, dispose=self.dispose,
                        income=self.income, price=price)

    def ltv_cap(self, price: float = float("inf")) -> float:
        """제도 상한과 희망 LTV 중 작은 쪽."""
        cap = reg.ltv_of(self.tier(price), self.regulated)
        return min(cap, self.ltv) if self.ltv is not None else cap

    def loan_cap(self, price: float) -> float:
        """수도권·규제지역 주택가격별 절대한도(만원)."""
        return reg.loan_cap(price, metro=self.metro, regulated=self.regulated)

    def stress(self) -> float:
        if self.stress_bp is not None:
            return max(0.0, float(self.stress_bp))
        return reg.stress_rate(metro=self.metro, regulated=self.regulated,
                               rate_type=self.rate_type)

    def term(self) -> int:
        """실제 적용 만기 — 수도권·규제지역은 30년으로 잘린다."""
        return reg.max_years(self.years, metro=self.metro, regulated=self.regulated)

    def bangongje(self) -> float:
        """방공제 차감액(만원). apply_bangongje=False 이면 0."""
        if not self.apply_bangongje:
            return 0.0
        return reg.bangongje_of(self.region, self.sido)


def broker_fee(price: float) -> float:
    """매매 중개보수(만원) — 구간 요율 상한 기준."""
    for cap, rate, limit in BROKER_BRACKETS:
        if price < cap:
            fee = price * rate
            return min(fee, limit) if limit else fee
    return price * BROKER_BRACKETS[-1][1]


def _base_tax_rate(price: float, homes: int, regulated: bool, *,
                   temp_two_home: bool = False) -> float:
    """취득세 본세율. 1주택(무주택 포함)은 가격 구간 1~3%, 다주택은 중과.

    일시적 2주택(구입 전 1주택 + 처분 예정)이면 중과 대신 1주택 세율을 쓴다.
    """
    억 = price / 10_000
    single = 0.01 if 억 <= 6 else (억 * 2 / 3 - 3) / 100 if 억 <= 9 else 0.03
    if homes <= 0:
        return single
    if homes == 1:
        if temp_two_home:
            return single
        return 0.08 if regulated else single
    return 0.12 if regulated else 0.08


def acq_tax(price: float, p: Params) -> float:
    """취득세 + 지방교육세 + 농특세(만원). 생애최초 감면·일시적 2주택 반영."""
    homes = max(0, min(2, int(p.homes or 0)))
    base = _base_tax_rate(price, homes, bool(p.regulated),
                          temp_two_home=bool(p.temp_two_home))
    edu = base * _EDU_TAX_SHARE
    farm = _BIG_AREA_FARM_TAX if p.big_area else 0.0
    tax = price * (base + edu + farm)
    if p.first_time and homes == 0 and price <= DEFAULTS["생애최초감면상한가"]:
        tax = max(0.0, tax - DEFAULTS["생애최초감면"])
    return tax


def legal_fee(price: float) -> float:
    """법무·소유권이전 등기 대행비 근사(만원)."""
    for cap, fee in LEGAL_BRACKETS:
        if price < cap:
            return float(fee)
    return float(LEGAL_BRACKETS[-1][1])


def stamp_tax(loan: float) -> float:
    """대출 인지세(만원)."""
    loan = max(0.0, float(loan or 0))
    for cap, fee in STAMP_BRACKETS:
        if loan <= cap:
            return float(fee)
    return float(STAMP_BRACKETS[-1][1])


def assessed_value(price: float) -> float:
    """시가표준액 근사(만원) — 등록면허세·국민주택채권용."""
    return max(0.0, float(price or 0)) * DEFAULTS["시가표준비율"]


def registry_tax(price: float) -> float:
    """등록면허세(만원) ≈ 시가표준 × 0.2%."""
    return assessed_value(price) * _REGISTRY_RATE


def housing_bond_cost(price: float) -> float:
    """국민주택채권 즉시매각 실부담(만원)."""
    face = assessed_value(price) * DEFAULTS["채권매입률"]
    return face * DEFAULTS["채권할인율"]


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
    stressed = p.rate + p.stress()
    return available / (monthly_payment(1.0, stressed, p.term()) * 12)


def loan_for(price: float, p: Params) -> dict:
    """그 가격에 실제로 나오는 대출액 — LTV·DSR·절대한도 중 최저, 방공제 차감."""
    tier = p.tier(price)
    ltv = p.ltv_cap(price)
    by_ltv = ltv * price
    by_dsr = dsr_loan_cap(p)
    by_cap = p.loan_cap(price)
    approved = max(0.0, min(by_ltv, by_dsr, by_cap))
    deduct = min(approved, p.bangongje()) if approved > 0 else 0.0
    loan = max(0.0, approved - deduct)
    binding = "LTV"
    if by_dsr <= by_ltv and by_dsr <= by_cap:
        binding = "DSR"
    elif by_cap < by_ltv:
        binding = "한도"
    return {"대출": loan, "승인액": approved, "방공제": deduct,
            "자격": tier, "LTV상한": ltv, "제약": binding,
            "LTV기준": by_ltv, "DSR기준": by_dsr, "절대한도": by_cap}


def cash_needed(price: float, p: Params, loan: float) -> dict:
    """그 가격을 사는 데 필요한 현금 분해(만원)."""
    tax = acq_tax(price, p)
    fee = broker_fee(price)
    legal = legal_fee(price)
    stamp = stamp_tax(loan)
    registry = registry_tax(price)
    bond = housing_bond_cost(price)
    move = max(0.0, p.moving_cost)
    repair = max(0.0, p.repair_cost)
    equity = max(0.0, price - loan)
    extras = tax + fee + legal + stamp + registry + bond + move + repair
    return {
        "자기자본투입": equity,
        "취득세": tax,
        "중개비": fee,
        "법무비": legal,
        "인지세": stamp,
        "등록면허세": registry,
        "국민주택채권": bond,
        "이사비": move,
        "수리비": repair,
        "합계": equity + extras,
    }


def for_price(price: float, p: Params) -> dict:
    """특정 가격을 살 때의 대출·월상환·필요현금. 예산 초과 여부 포함."""
    lim = loan_for(price, p)
    loan = lim["대출"]
    cash = cash_needed(price, p, loan)
    m = monthly_payment(loan, p.rate, p.term())
    out = {
        "매수가": round(price),
        "대출": round(loan),
        "실효LTV": round(loan / price, 3) if price else 0.0,
        "월상환": round(m),
        "필요현금": round(cash["합계"]),
        "부족": round(max(0.0, cash["합계"] - p.capital)),
        "가능": cash["합계"] <= p.capital + 1,
        "제약": lim["제약"],
        "자격": lim["자격"],
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
    """자기자본으로 살 수 있는 최대 매수가(만원) + 비용 분해.

    LTV·절대한도가 가격 구간마다 달라져 필요현금이 계단식으로 뛰지만, 가격이 오를수록
    필요현금은 단조 증가하므로 이분탐색이 성립한다.
    """
    def affordable(price: float) -> bool:
        return cash_needed(price, p, loan_for(price, p)["대출"])["합계"] <= p.capital

    price = round(_max_price_where(p, affordable))
    lim = loan_for(price, p)
    loan = round(lim["대출"])
    cash = cash_needed(price, p, loan)
    # 최대가에서는 현금이 항상 소진되므로, 남은 정보는 '대출을 무엇이 막았나'다.
    constraint = lim["제약"] if (price > 0 and loan > 0) else "자본"
    cost_keys = ("취득세", "중개비", "법무비", "인지세", "등록면허세",
                 "국민주택채권", "이사비", "수리비")
    detail = {
        "대출": loan,
        "승인액": round(lim["승인액"]),
        "방공제": round(lim["방공제"]),
        **{k: round(cash[k]) for k in cost_keys},
        "자기자본": round(p.capital),
        "월상환": round(monthly_payment(loan, p.rate, p.term())),
        "실효LTV": round(loan / price, 3) if price else 0.0,
        "자격": lim["자격"],
        "LTV상한": lim["LTV상한"],
        "절대한도": None if lim["절대한도"] == float("inf") else round(lim["절대한도"]),
        "제약": constraint,
        "DSR제약": constraint == "DSR",
    }
    return price, detail


def safe_purchase(p: Params) -> dict | None:
    """월 상환액이 월소득의 '안전상환비율' 이내인 최대 매수가. 소득 없으면 None."""
    if not p.income or p.income <= 0:
        return None
    ceiling = p.income / 12 * DEFAULTS["안전상환비율"]

    def ok(price: float) -> bool:
        loan = loan_for(price, p)["대출"]
        if cash_needed(price, p, loan)["합계"] > p.capital:
            return False
        return monthly_payment(loan, p.rate, p.term()) <= ceiling

    price = round(_max_price_where(p, ok))
    if price <= 0:
        return None
    return for_price(price, p)


def notes(p: Params, price: float) -> list[str]:
    """규제 때문에 숫자가 이렇게 나온 이유 — 확정서에 그대로 붙는다."""
    out = []
    tier = p.tier(price)
    if not p.region:
        out.append("매수 지역을 아직 안 정해서 수도권 규제지역(LTV·절대한도·스트레스)으로 보수 계산했어요. 지역을 넣으면 맞춰 드려요.")
    if p.regulated:
        out.append(f"규제지역이라 {reg.TIER_LABEL[tier]} LTV {int(reg.ltv_of(tier, True) * 100)}%가 적용돼요.")
    if tier == "1주택_유지":
        out.append("규제지역에서 기존 주택을 안 팔면 주담대가 아예 안 나와요(6개월 처분·전입 약정 필요)."
                   if p.regulated else "기존 주택을 유지하면 LTV가 60%로 내려가요.")
    cap = p.loan_cap(price)
    if cap != float("inf"):
        out.append(f"수도권·규제지역 절대한도 — 시가 {price / 10_000:.1f}억이면 대출은 최대 {cap / 10_000:.0f}억이에요.")
    if p.metro or p.regulated:
        out.append(f"DSR 심사금리는 스트레스 {p.stress() * 100:.2f}%p를 더한 "
                   f"{(p.rate + p.stress()) * 100:.2f}% ({p.rate_type}형 기준), 만기는 최장 {p.term()}년이에요.")
        out.append(f"주담대를 받으면 {reg.MOVE_IN_MONTHS}개월 안에 전입해야 해요.")
    if p.apply_bangongje:
        bg = p.bangongje()
        if bg > 0:
            out.append(f"{reg.BANGONGJE_NOTE} (이 지역 {bg / 10_000:.1f}억 차감).")
    if p.temp_two_home and int(p.homes or 0) == 1:
        out.append("일시적 2주택으로 취득세 중과를 피하고 1주택 세율로 계산했어요. 기한 내 기존 주택 처분이 전제예요.")
    if p.big_area:
        out.append("전용 85㎡ 초과라 농어촌특별세(0.2%)가 붙어요.")
    if p.first_time:
        out.append(reg.POLICY_LOAN_NOTE)
    out.append(reg.DISCLAIMER)
    return out


def statement(p: Params) -> dict:
    """매수력 확정서 — UI·브리핑·숏리스트가 공통으로 쓰는 단일 산출물."""
    price, detail = max_purchase(p)
    safe = safe_purchase(p)
    cost_keys = ("취득세", "중개비", "법무비", "인지세", "등록면허세",
                 "국민주택채권", "이사비", "수리비")
    out = {
        "최대매수가": price,
        "대출": detail["대출"],
        "승인액": detail["승인액"],
        "방공제": detail["방공제"],
        "실효LTV": detail["실효LTV"],
        "월상환": detail["월상환"],
        "제약": detail["제약"],
        "비용": {k: detail[k] for k in cost_keys},
        "필요현금": round(cash_needed(price, p, detail["대출"])["합계"]),
        "안정매수가": safe["매수가"] if safe else None,
        "안정월상환": safe["월상환"] if safe else None,
        "LTV상한": detail["LTV상한"],
        "규제": {
            "기준일": reg.AS_OF,
            "지역": p.region,
            "지역가정": p.region is None,   # True면 규제지역으로 보수 가정 중
            "규제지역": bool(p.regulated),
            "수도권": bool(p.metro),
            "자격": detail["자격"],
            "자격설명": reg.TIER_LABEL[detail["자격"]],
            "절대한도": detail["절대한도"],
            "방공제": detail["방공제"],
            "방공제구간": reg.bangongje_zone(p.region, p.sido) if p.apply_bangongje else None,
            "스트레스금리": round(p.stress(), 4),
            "심사금리": round(p.rate + p.stress(), 4),
            "적용만기": p.term(),
        },
        "안내": notes(p, price),
        "가정": {
            "자기자본": round(p.capital),
            "연소득": round(p.income) if p.income else None,
            "기대출연원리금": round(p.existing_debt_annual or 0),
            "주택수": int(p.homes or 0),
            "생애최초": bool(p.first_time),
            "일시적2주택": bool(p.temp_two_home),
            "전용85초과": bool(p.big_area),
            "방공제적용": bool(p.apply_bangongje),
            "지역": p.region,
            "규제지역": bool(p.regulated),
            "수도권": bool(p.metro),
            "처분약정": bool(p.dispose),
            "희망LTV": p.ltv,
            "금리": p.rate,
            "금리유형": p.rate_type,
            "만기": p.years,
            "스트레스가산": round(p.stress(), 4),
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
        "temp_two_home": bool(conf.get("일시적2주택", False)),
        "big_area": bool(conf.get("전용85초과", False)),
        "apply_bangongje": conf.get("방공제적용", True) is not False,
        "region": pick("매수지역", "지역"),
        "regulated": bool(conf.get("규제지역")),
        "dispose": bool(conf.get("처분약정", True)),
        "ltv": conf.get("희망LTV"),
        "rate": conf.get("금리") or DEFAULTS["금리"],
        "rate_type": conf.get("금리유형") or reg.DEFAULT_RATE_TYPE,
        "years": int(conf.get("만기") or DEFAULTS["만기"]),
    }
    base.update({k: v for k, v in override.items() if v is not None})
    valid = set(Params.__dataclass_fields__)
    return Params(**{k: v for k, v in base.items() if k in valid})


def params_for_region(p: Params, region: str | None, sido: str | None = None) -> Params:
    """같은 재무조건을 다른 지역 규제로 다시 본다 — 숏리스트 후보별 자금 계산용."""
    if not region:
        return p
    from dataclasses import replace
    return replace(p, region=region, sido=sido)


def as_dict(p: Params) -> dict:
    return asdict(p)
