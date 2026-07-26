"""주담대 규제 테이블 — 지역·주택수·자격에 따른 LTV·대출한도·스트레스금리.

기준일 AS_OF. 근거:
- 6·27 가계부채 관리방안(2025.6.28~): 수도권·규제지역 주담대 최대 6억, 만기 30년,
  6개월 내 전입, 생애최초 LTV 80%→70%, 다주택 0%.
- 10·15 주택시장 안정화 대책(2025.10.16~): 규제지역 = 서울 25개 구 + 경기 12곳,
  무주택 LTV 70%→40%, 1주택 처분미약정 0%, 서민·실수요자 60%,
  대출한도 주택가격 차등(15억↓ 6억 / 15~25억 4억 / 25억↑ 2억),
  수도권·규제지역 스트레스금리 하한 1.5%→3.0%.
- 2026.7.1~: 화성 동탄·용인 기흥·구리 규제지역 추가 지정(총 40곳).

생애최초 LTV 우대는 지역·주택가격·소득과 무관하다(2022.8 감독규정 개정). 비율만
6·27/10·15 로 축소됐다. 다만 주금공 보증 기반 상품(디딤돌·보금자리·은행 생애최초
특례보증)은 별도의 가격·소득 요건이 붙으므로 POLICY_LOAN_NOTE 로 안내만 한다.
"""

from __future__ import annotations

AS_OF = "2026-07-01"

METRO_SIDO = ("서울", "경기", "인천")

# 서울은 25개 구 전역이 규제지역.
SEOUL_GU = {
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구",
    "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구",
    "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구",
    "강동구",
}
# 서울 밖 광역시에도 같은 이름이 있는 구 — 시도 확인 없이는 규제로 볼 수 없다.
_AMBIGUOUS_GU = {"중구", "강서구"}

REGULATED_GYEONGGI = {
    "과천시", "광명시", "하남시", "의왕시", "구리시",
    "수원시 장안구", "수원시 팔달구", "수원시 영통구",
    "성남시 수정구", "성남시 중원구", "성남시 분당구",
    "안양시 동안구", "용인시 수지구", "용인시 기흥구", "화성시 동탄구",
}

# 자격 → LTV 상한. '1주택'은 6개월 내 처분·전입 약정 여부로 갈린다.
LTV = {
    True: {   # 규제지역
        "생애최초": 0.70, "서민실수요": 0.60, "무주택": 0.40,
        "1주택_처분조건": 0.40, "1주택_유지": 0.00, "다주택": 0.00,
    },
    False: {  # 비규제지역
        "생애최초": 0.80, "서민실수요": 0.70, "무주택": 0.70,
        "1주택_처분조건": 0.70, "1주택_유지": 0.60, "다주택": 0.60,
    },
}
TIER_LABEL = {
    "생애최초": "생애최초 우대", "서민실수요": "서민·실수요자 우대", "무주택": "무주택 일반",
    "1주택_처분조건": "1주택(6개월 처분·전입 약정)", "1주택_유지": "1주택 처분 미약정",
    "다주택": "다주택",
}

# 서민·실수요자 요건 (무주택 세대주). 만원 단위.
SPECIAL_INCOME_CAP = 9_000
SPECIAL_PRICE_CAP = 80_000

# 수도권·규제지역 주담대 절대한도 — (주택가격 상한, 대출한도). 만원.
LOAN_CAP_BRACKETS = ((150_000, 60_000), (250_000, 40_000), (float("inf"), 20_000))

# 스트레스 DSR 3단계 기준 스트레스금리. 지방은 2026.12.31 까지 2단계(0.75%p) 유예.
STRESS_BASE = {True: 0.030, False: 0.0075}
# 금리유형별 적용비율. 순수고정(만기 70% 이상 고정)은 미적용.
STRESS_RATIO = {"변동": 1.0, "혼합": 0.6, "주기": 0.3, "고정": 0.0}
DEFAULT_RATE_TYPE = "혼합"

MAX_YEARS_METRO = 30      # 수도권·규제지역 주담대 최장 만기
MOVE_IN_MONTHS = 6        # 전입 의무
DISPOSE_MONTHS = 6        # 1주택 처분 기한

POLICY_LOAN_NOTE = (
    "디딤돌·보금자리론·은행 생애최초 특례보증은 부부합산 소득·주택가격 요건이 따로 있어 "
    "여기 계산과 한도가 다를 수 있어요."
)
DISCLAIMER = f"{AS_OF} 기준 공개 규제 요약이며, 실제 한도는 은행 심사(방공제·신용도)에 따라 달라집니다."


def _norm(region: str | None) -> str:
    """'서울 강남구' → '강남구'. 앞에 붙은 시도 접두어를 떼어 구 이름만 남긴다."""
    r = (region or "").strip()
    for sido in METRO_SIDO:
        if r.startswith(sido + " "):
            return r[len(sido) + 1:].strip()
    return r


def sido_hint(region: str | None) -> str | None:
    """지역명 자체에 시도가 붙어 있으면 그것을 쓴다."""
    r = (region or "").strip()
    for sido in METRO_SIDO:
        if r.startswith(sido + " ") or r == sido:
            return sido
    return None


def is_regulated(region: str | None, sido: str | None = None) -> bool:
    """규제지역(조정대상지역·투기과열지구) 여부."""
    name = _norm(region)
    sido = sido or sido_hint(region)
    if name in REGULATED_GYEONGGI:
        return sido in (None, "경기")
    if name in SEOUL_GU:
        return sido == "서울" if name in _AMBIGUOUS_GU else sido in (None, "서울")
    return False


def is_metro(region: str | None, sido: str | None = None) -> bool:
    """수도권(서울·경기·인천) 여부 — 절대한도·스트레스금리 3.0%p 적용 범위."""
    sido = sido or sido_hint(region)
    if sido:
        return sido in METRO_SIDO
    return is_regulated(region)


# 40곳 모두 투기과열지구·조정대상지역·토지거래허가구역 삼중 지정(토허는 아파트 한정).
DESIGNATION_TAGS = ("투기과열지구", "조정대상지역", "토지거래허가구역")
# 지도가 구 단위로 못 쪼개는 곳 — 시 전체가 아니라 일부만 규제다.
PARTIAL_NOTE = {"화성시": "동탄구만 규제 (2026.7.1~)"}


def regulated_regions() -> list[str]:
    """규제지역 전체 목록 — 지도 오버레이·자문 tool 공용 정본."""
    return sorted(SEOUL_GU) + sorted(REGULATED_GYEONGGI)


def designations(region: str | None, sido: str | None = None) -> list[str]:
    """해당 지역의 지정 현황. 공백 표기차('성남시분당구')를 흡수한다."""
    name = _norm(region).replace(" ", "")
    sido = sido or sido_hint(region)
    hit = any(r.replace(" ", "") == name for r in REGULATED_GYEONGGI) and sido in (None, "경기")
    if not hit and name in SEOUL_GU:
        hit = sido == "서울" if name in _AMBIGUOUS_GU else sido in (None, "서울")
    return list(DESIGNATION_TAGS) if hit else []


def classify(region: str | None, sido: str | None = None) -> dict:
    reg = is_regulated(region, sido)
    return {"지역": region, "규제지역": reg, "수도권": reg or is_metro(region, sido)}


def tier(homes: int, *, first_time: bool, dispose: bool,
         income: float | None, price: float) -> str:
    """차주 자격 구분. 가격·소득에 따라 서민·실수요자 우대가 붙는다."""
    homes = max(0, min(2, int(homes or 0)))
    if homes >= 2:
        return "다주택"
    if homes == 1:
        return "1주택_처분조건" if dispose else "1주택_유지"
    if first_time:
        return "생애최초"
    if income is not None and income <= SPECIAL_INCOME_CAP and price <= SPECIAL_PRICE_CAP:
        return "서민실수요"
    return "무주택"


def ltv_of(tier_name: str, regulated: bool) -> float:
    return LTV[bool(regulated)][tier_name]


def loan_cap(price: float, *, metro: bool, regulated: bool) -> float:
    """수도권·규제지역 주택가격별 대출 절대한도(만원). 그 밖은 제한 없음."""
    if not (metro or regulated):
        return float("inf")
    for ceiling, cap in LOAN_CAP_BRACKETS:
        if price <= ceiling:
            return float(cap)
    return float(LOAN_CAP_BRACKETS[-1][1])


def stress_rate(*, metro: bool, regulated: bool, rate_type: str = DEFAULT_RATE_TYPE) -> float:
    """DSR 한도 산정용 가산금리. 실제 납입금리에는 영향 없다."""
    base = STRESS_BASE[bool(metro or regulated)]
    return base * STRESS_RATIO.get(rate_type, STRESS_RATIO[DEFAULT_RATE_TYPE])


def max_years(years: int, *, metro: bool, regulated: bool) -> int:
    years = max(1, int(years or MAX_YEARS_METRO))
    return min(years, MAX_YEARS_METRO) if (metro or regulated) else years
