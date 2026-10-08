"""경매 매물 관리 + 과거 계산 모델.

실제 화면의 목적별 시뮬레이션은 ``auction_bid_engine``을 사용한다. 아래의
``breakdown``/``table``/``recommend``는 과거 모델의 호환·회귀 비교 코드이며
목록, 계산 API, 낙찰 후 플랜에서 자동 상한으로 사용하지 않는다.

과거 계산 모델:
  - 경매 총매입비용 = 입찰가 + 등기비 + 명도비 + 미납관리비 + 수리비 + 대리입찰 + 인수보증금 + 보유이자
  - 일반매매 총매입비용 = 시세 + 취득세 + 중개수수료 + 법무비
  - 총비용우위 = 일반매매총매입 − 경매총매입. 매도 이익/수익률이 아니다.
  - 실투자금 = 경매총매입 − 대출금(=입찰가×대출비율)
  - 임대수익률 = (월세×12 − 대출금×금리) / (실투자금 − 임대보증금)
  - 단기매도 순수익 = 매도가 − 경매총매입 − 매도중개보수
낙찰가율(감정가 대비)을 1%씩 변화시킨 민감도 표를 만들고,
목표 총비용 우위율을 만족하는 검토용 입찰 상한을 도출한다.

매물은 수동입력/CSV → data/cache/auction.json.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from math import isfinite
from pathlib import Path

AUCTION_FILE = Path("data/cache/auction.json")

# 계산 기본 파라미터 (기준값; 모두 조정 가능)
DEFAULTS = {
    "취득세율": 0.011,        # 등기비: 입찰가×취득세율 + 법무비
    "매수중개율": 0.005,      # 일반매매 취득 시 중개수수료
    "매도중개율": 0.005,      # 단기매도 시 중개보수
    "법무비": 1_000_000,
    "명도_평당": 150_000,     # 전용 평당 명도비(강제집행 기준)
    "㎡_평": 0.3025,
    "대출비율": 0.7,
    "대출금리": 0.05,
    "보유개월": 6,            # 단기매도/이자 가정 개월
    "목표시세차익률": 0.10,   # 호환 키: 일반매매 대비 총비용 우위율 목표
}

CSV_FIELDS = ["사건번호", "단지명", "region", "감정가", "최저매각가", "유찰횟수",
              "입찰기일", "시세", "전용면적", "대출비율", "월임대료", "임대보증금",
              "매도가", "미납관리비", "수리비", "메모"]


@dataclass
class Listing:
    사건번호: str = ""
    단지명: str = ""
    region: str = ""
    감정가: float = 0.0          # 만원
    최저매각가: float | None = None
    입찰보증금: float | None = None  # 법원 사건별 공고 금액(만원), 일률 10%로 확정하지 않음
    유찰횟수: int = 0
    입찰기일: str = ""
    시세: float | None = None
    전용면적: float = 0.0        # ㎡
    대출비율: float | None = None
    대출금리: float | None = None
    월임대료: float = 0.0        # 만원
    임대보증금: float = 0.0      # 만원
    매도가: float = 0.0          # 단기매도 예상가(만원)
    미납관리비: float = 0.0
    수리비: float = 0.0
    인수보증금: float | None = None  # 미확인과 인수액 0을 구별한다
    대리입찰비: float = 0.0
    권리분석: dict = field(default_factory=dict)
    낙찰가: float | None = None   # 낙찰 후 플랜 기준가
    낙찰일: str = ""
    매각허가결정일: str = ""       # 법원 통지 기준. D+7 같은 고정 추정일이 아님
    대금지급기한: str = ""         # 법원 통지 기준
    최근실거래가: float | None = None   # 국토부 동일단지 최근 매매(만원)
    실거래표본: list[dict] = field(default_factory=list)  # 국토부 동일단지·전용면적 ±0.1㎡
    실거래표본갱신일: str = ""           # KST 날짜, 과거 자료의 현재성 오인 방지
    최근전세가: float | None = None     # 국토부 동일단지 최근 전세(만원)
    건축년도: int | None = None         # 국토부 실거래 매칭 단지 건축년도
    용적률: float | None = None         # 건축물대장 총괄표제부(%)
    건폐율: float | None = None         # 건축물대장(%)
    세대수: int | None = None           # 건축물대장 단지 세대수
    최고층: int | None = None           # 건축물대장 최고층
    단지평단가: int | None = None        # 국토부 실거래 단지 평단가(만/평)
    단지급지: int | None = None          # 지역 내 급지 1~5 (1=최상급)
    급지상위: int | None = None          # 지역 내 상위 N%
    지역중앙대비: int | None = None      # 지역 중앙 평단가 대비 %
    메모: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


def _p(overrides: dict | None = None) -> dict:
    p = dict(DEFAULTS)
    if overrides:
        p.update({k: v for k, v in overrides.items() if v is not None})
    return p


def breakdown(lst: Listing, 입찰가: float, p: dict) -> dict:
    """주어진 입찰가에 대한 전체 비용·수익 분해."""
    loan_ratio = lst.대출비율 if lst.대출비율 is not None else p["대출비율"]
    rate = lst.대출금리 if lst.대출금리 is not None else p["대출금리"]
    market = lst.시세 or lst.최근실거래가 or lst.감정가  # 단독 계산용 대체값; 추천에는 실거래/시세 필수

    등기비 = 입찰가 * p["취득세율"] + p["법무비"] / 10000  # 법무비는 원→만원
    명도비 = lst.전용면적 * p["㎡_평"] * p["명도_평당"] / 10000
    부대 = 등기비 + 명도비 + lst.미납관리비 + lst.수리비 + lst.대리입찰비 + (lst.인수보증금 or 0)
    대출금 = 입찰가 * loan_ratio
    보유이자 = 대출금 * rate / 12 * p["보유개월"]
    경매총매입 = 입찰가 + 부대 + 보유이자

    매매총매입 = market * (1 + p["취득세율"] + p["매수중개율"]) + p["법무비"] / 10000
    비용우위 = 매매총매입 - 경매총매입
    비용우위율 = 비용우위 / 경매총매입 if 경매총매입 else 0.0
    실투자금 = 경매총매입 - 대출금

    # 임대수익률
    임대순수익 = lst.월임대료 * 12 - 대출금 * rate
    임대실투자 = 실투자금 - lst.임대보증금
    임대수익률 = 임대순수익 / 임대실투자 if 임대실투자 > 0 else None

    # 단기매도 순수익
    매도순수익 = 매도수익률 = None
    if lst.매도가:
        매도순수익 = lst.매도가 - 경매총매입 - lst.매도가 * p["매도중개율"]
        매도수익률 = 매도순수익 / 실투자금 if 실투자금 else None

    rnd = lambda x: None if x is None else round(x)
    pct = lambda x: None if x is None else round(x * 100, 1)
    return {
        "입찰가": rnd(입찰가), "등기비": rnd(등기비), "명도비": rnd(명도비),
        "대출금": rnd(대출금), "보유이자": rnd(보유이자), "경매총매입": rnd(경매총매입),
        "매매총매입": rnd(매매총매입),
        "총비용우위": rnd(비용우위), "총비용우위율": pct(비용우위율),
        "시세차익": rnd(비용우위), "시세차익률": pct(비용우위율),  # 기존 API 호환, 매도 차익 아님
        "실투자금": rnd(실투자금), "임대수익률": pct(임대수익률),
        "매도순수익": rnd(매도순수익), "매도수익률": pct(매도수익률),
    }


def _floor_rate(lst: Listing) -> float:
    if lst.감정가 and lst.최저매각가:
        return lst.최저매각가 / lst.감정가
    return 0.7  # 최저가 미상 시 70% 가정


def _bid_blockers(lst: Listing) -> list[str]:
    """입력 누락을 저가 입찰 기회로 바꾸지 않는다."""
    reasons = []
    if lst.감정가 <= 0 or not lst.최저매각가 or lst.최저매각가 <= 0:
        reasons.append("감정가·최저매각가 확인 필요")
    if not (lst.시세 or lst.최근실거래가) or max(lst.시세 or 0, lst.최근실거래가 or 0) <= 0:
        reasons.append("비교 가능한 시세·실거래가 확인 필요")
    if lst.전용면적 <= 0:
        reasons.append("전용면적 확인 필요")
    saved = lst.권리분석 or {}
    analysis = saved.get("분석") or {}
    if not saved.get("조사완료"):
        reasons.append("등기부·매각물건명세서·점유 확인 필요")
    if analysis.get("확인필요") or lst.인수보증금 is None or analysis.get("인수합계") is None:
        reasons.append("인수 권리·보증금 확인 필요")
    return reasons


def table(lst: Listing, p: dict, span: float = 0.30, step: float = 0.01) -> list[dict]:
    """낙찰가율(감정가 대비)별 민감도 표 (낮은 입찰가→높은 입찰가)."""
    if _bid_blockers(lst):
        return []
    floor = _floor_rate(lst)
    rows = []
    r = floor
    while r <= min(1.0, floor + span) + 1e-9:
        bid = round(lst.감정가 * r)
        rows.append({"낙찰가율": round(r * 100, 1), **breakdown(lst, bid, p)})
        r += step
    return rows


def recommend(lst: Listing, p: dict) -> dict:
    """검토용 상한. 권리·시세가 불명확하거나 목표 미달이면 금액을 내지 않는다."""
    reasons = _bid_blockers(lst)
    if reasons:
        return {"상태": "needs_review", "입찰가": None, "사유": reasons}
    rows = table(lst, p)
    if not rows:
        return {"상태": "insufficient_evidence", "입찰가": None,
                "사유": ["유효한 입찰가 구간이 없습니다. 최저매각가를 확인하세요."]}
    target = p["목표시세차익률"] * 100
    ok = [row for row in rows if row["총비용우위율"] is not None and row["총비용우위율"] >= target]
    if not ok:
        return {"상태": "no_bid", "입찰가": None,
                "사유": [f"최저매각가에서도 목표 총비용 우위율 {target:g}%를 충족하지 못합니다."],
                "최저가총비용우위율": rows[0]["총비용우위율"],
                "최저가시세차익률": rows[0]["총비용우위율"]}
    return {**max(ok, key=lambda x: x["입찰가"]), "상태": "conditional_bid", "사유": []}


# --- 저장소 ---
def load() -> list[Listing]:
    if not AUCTION_FILE.exists():
        return []
    return [Listing(**d) for d in json.loads(AUCTION_FILE.read_text(encoding="utf-8"))]


def save(listings: list[Listing]) -> None:
    AUCTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUCTION_FILE.write_text(
        json.dumps([asdict(x) for x in listings], ensure_ascii=False, indent=2), encoding="utf-8")


def add(data: dict) -> Listing:
    listings = load()
    lst = Listing(**{k: v for k, v in data.items() if k in Listing.__dataclass_fields__})
    listings.append(lst)
    save(listings)
    return lst


def remove(listing_id: str) -> None:
    save([x for x in load() if x.id != listing_id])


def get(listing_id: str) -> Listing | None:
    return next((x for x in load() if x.id == listing_id), None)


def update(listing_id: str, patch: dict) -> Listing | None:
    listings = load()
    hit = next((x for x in listings if x.id == listing_id), None)
    if hit is None:
        return None
    for k, v in patch.items():
        if k in Listing.__dataclass_fields__ and k != "id":
            setattr(hit, k, v)
    save(listings)
    return hit


# --- 낙찰 후 실행 플랜 ---
def plan(lst: Listing, 낙찰가: float | None = None, p: dict | None = None) -> dict:
    """입력한 사건별 기일만 일정에 표시한다. 누락된 법원 기한을 D+일로 만들지 않는다."""

    pp = _p(p)
    bid = 낙찰가 if 낙찰가 is not None else lst.낙찰가
    assumed = False  # 자동 산정 입찰가를 플랜의 사실값으로 사용하지 않는다
    if bid is None or bid <= 0:
        if bid is not None and bid < 0:
            return {"상태": "invalid_assumption", "사유": ["낙찰가가 0보다 커야 합니다."],
                    "낙찰가": None, "총현금": None, "steps": []}
        return {"상태": "needs_review", "사유": ["입찰가 또는 실제 낙찰가를 먼저 입력하세요."], "낙찰가": None,
                "총현금": None, "steps": []}
    base_day = _date_of(lst.낙찰일) or _date_of(lst.입찰기일)
    if base_day is None:
        return {"상태": "needs_review", "사유": ["법원 입찰기일 또는 실제 낙찰일 확인이 필요합니다."],
                "낙찰가": None, "총현금": None, "steps": []}

    raw_deposit = pp.get("입찰보증금") if pp.get("입찰보증금") is not None else lst.입찰보증금
    try:
        deposit_value = float(raw_deposit)
    except (TypeError, ValueError):
        deposit_value = float("nan")
    if not isfinite(deposit_value) or not 0 < deposit_value <= bid:
        return {"상태": "needs_review", "사유": ["법원 공고의 입찰보증금 금액을 확인하세요."],
                "낙찰가": None, "총현금": None, "steps": []}
    보증금 = round(deposit_value)
    등기비 = round(bid * pp["취득세율"] + pp["법무비"] / 10000)
    명도비 = round(lst.전용면적 * pp["㎡_평"] * pp["명도_평당"] / 10000)
    try:
        loan_ratio = float(lst.대출비율 if lst.대출비율 is not None else pp["대출비율"])
    except (TypeError, ValueError):
        loan_ratio = float("nan")
    if not isfinite(loan_ratio) or not 0 <= loan_ratio <= 1:
        return {"상태": "invalid_assumption", "사유": ["대출비율은 0~1 범위여야 합니다."],
                "낙찰가": None, "총현금": None, "steps": []}
    # 보증금은 이미 납부한 것으로 보고 잔금에서 쓸 수 있는 가정 대출만 표시한다.
    대출 = min(round(bid * loan_ratio), max(0, round(bid - 보증금)))
    잔금 = max(0, round(bid - 보증금 - 대출))
    보유이자 = round(대출 * (lst.대출금리 if lst.대출금리 is not None else pp["대출금리"])
                 / 12 * pp["보유개월"])
    money = [-(보증금 + lst.대리입찰비), -(잔금 + 등기비),
             -(명도비 + lst.미납관리비 + lst.수리비)]
    # 인수보증금 반환 시점과 보유이자 지급 시점은 사건·대출별로 달라 날짜를 단정하지 않는다.
    undated = (round(lst.인수보증금) + 보유이자) if lst.인수보증금 is not None else None
    assumed_cash = (sum(-x for x in money) + undated) if undated is not None else None
    no_loan_cash = (assumed_cash + 대출 - 보유이자) if assumed_cash is not None else None
    steps = []
    for title, raw_date, todo, amount in (
        ("입찰·낙찰", lst.낙찰일 or lst.입찰기일,
         "보증금 영수증과 법원 공고를 보관하세요.", money[0]),
        ("매각허가결정", lst.매각허가결정일,
         "법원 결정과 이의·항고 여부를 확인하세요.", None),
        ("대금지급기한", lst.대금지급기한,
         "법원 통지의 기한과 잔금·대출 실행액을 확인하세요.", money[1]),
        ("명도·수리", "", "점유자 협의·수리 시점은 사건별로 확인하세요.", money[2]),
    ):
        day = _date_of(raw_date)
        steps.append({"D": (day - base_day).days if day else None,
                      "날짜": day.isoformat() if day else None,
                      "단계": title, "할일": todo,
                      "금액": round(amount) if amount else None})
    return {
        "기준일": base_day.isoformat(), "낙찰가": round(bid), "추정입찰가": assumed,
        "보증금": 보증금, "경락잔금대출": 대출, "잔금": 잔금,
        "대출상태": "미승인_가정", "대출확인필요": True, "대출비율가정": loan_ratio,
        "등기비": 등기비, "명도비": 명도비,
        "대리입찰비": round(lst.대리입찰비), "보유이자": 보유이자,
        "일정상태": "법원기한확인필요" if not lst.대금지급기한 else "입력기한기준",
        "날짜미정현금": undated,
        "가정시필요현금": assumed_cash, "무대출필요현금": no_loan_cash,
        "총현금": assumed_cash,  # 기존 API 호환: 승인된 대출 기준의 확정 현금이 아님
        "사유": (["대출비율은 승인액이 아닌 가정입니다. 입찰 전 은행 심사와 잔금일 자금 조달을 확인하세요."]
               + (["법원 매각허가결정일·대금지급기한을 입력하기 전에는 일정 날짜를 비웁니다."]
                  if not lst.매각허가결정일 or not lst.대금지급기한 else [])
               + (["인수 보증금이 미확정이므로 필요현금 합계를 계산하지 않습니다."]
                  if lst.인수보증금 is None else [])
               + (["인수보증금·보유이자는 지급 시점이 달라 일정표 밖 예비현금으로 표시합니다."]
                  if undated else [])),
        "steps": steps,
    }


def _date_of(s: str):
    from datetime import date

    t = (s or "").strip().replace(".", "-").replace("/", "-")[:10]
    try:
        y, m, d = (int(x) for x in t.split("-")[:3])
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


# --- 붙여넣기 파서(규칙) — AI 키 없이도 임포트가 되게 ---
_RE_CASE = re.compile(r"(20\d{2})\s*타\s*경\s*(\d+)")
_RE_MONEY = re.compile(r"([\d,]{4,})\s*원")
_RE_DATE = re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})")
_RE_AREA = re.compile(r"(?:전용|전용면적)?\s*([\d]{2,3}\.?\d*)\s*(?:㎡|m2|m²)")
_RE_SGG = re.compile(r"[가-힣]{2,8}(?:시|군|구)")
_SIDO = ("특별시", "광역시", "특별자치시", "특별자치도")
_RE_APT = re.compile(r"([가-힣A-Za-z0-9()·\s]{2,20}?(?:아파트|마을|타운|캐슬|자이|힐스테이트|푸르지오|래미안|e편한세상|더샵|주공\s?\d*|한양수자인))")


def _won_to_man(s: str) -> float:
    return round(int(s.replace(",", "")) / 10000)


def _sigungu(text: str) -> str:
    """소재지 → 시군구. 시도(특별시·광역시·도)는 버리고, '성남시 분당구'처럼 시+구는 붙인다."""
    toks = [t for t in _RE_SGG.findall(text) if not t.endswith(_SIDO)]
    if not toks:
        return ""
    if len(toks) >= 2 and toks[0].endswith("시") and toks[1].endswith("구"):
        return f"{toks[0]} {toks[1]}"
    return toks[0]


def parse_text(text: str) -> dict:
    """법원경매 공고 텍스트 → Listing 필드. 못 뽑은 건 넣지 않는다(빈값 덮어쓰기 방지)."""
    t = (text or "").replace("\u00a0", " ")
    out: dict = {}
    if m := _RE_CASE.search(t):
        out["사건번호"] = f"{m.group(1)}타경{m.group(2)}"
    if m := _RE_APT.search(t):
        out["단지명"] = re.sub(r"\s+", " ", m.group(1)).strip()
    if region := _sigungu(t):
        out["region"] = region
    for label, field_name in (("감정", "감정가"), ("최저", "최저매각가")):
        if m := re.search(rf"{label}[^\n]{{0,12}}?([\d,]{{5,}})\s*원", t):
            out[field_name] = _won_to_man(m.group(1))
    if "감정가" not in out and (m := _RE_MONEY.search(t)):
        out["감정가"] = _won_to_man(m.group(1))
    if m := re.search(r"(?:입찰|매각)\s*기일[^\n]{0,20}?(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})", t):
        out["입찰기일"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    elif m := _RE_DATE.search(t):
        out["입찰기일"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if m := _RE_AREA.search(t):
        out["전용면적"] = float(m.group(1))
    if m := re.search(r"유찰\s*(\d)\s*회", t):
        out["유찰횟수"] = int(m.group(1))
    elif t.count("유찰"):
        out["유찰횟수"] = t.count("유찰")
    return out


def parse_confidence(parsed: dict) -> str:
    """규칙 파서 결과가 쓸 만한지 — 부족하면 호출부가 AI 파서로 넘어간다."""
    core = sum(1 for k in ("사건번호", "단지명", "감정가", "입찰기일") if parsed.get(k))
    return {4: "high", 3: "high", 2: "medium"}.get(core, "low")


def _norm(s: str) -> str:
    # 한글·숫자만 남김 (공백·괄호·특수문자·'아파트' 표기차 제거)
    return re.sub(r"[^가-힣0-9]", "", (s or "").replace("아파트", ""))


def _recent_yms(n: int = 6) -> list[str]:
    from realty_signal.time_kst import previous_months
    return previous_months(n)


def recent_trade_price(lawd5: str, dong: str, core: str, area: float, key: str,
                       with_samples: bool = False):
    """국토부 매매 실거래에서 동일단지(동+이름+면적) 최근 (거래금액 만원, 건축년도, 지번).

    지번 = (bjdongCd, bonbun, bubun) — 건축물대장 조회용. 매칭 실패 시 (None, None, None).
    """
    base = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    best = None
    samples = []
    cn = _norm(core)
    for ym in _recent_yms(6):
        from realty_signal.ingest.complex import _items
        for it in _items(base, lawd5, key, ym):
            apt = (it.findtext("aptNm") or "").strip()
            umd = (it.findtext("umdNm") or "").strip()
            if dong and umd and dong not in umd and umd not in dong:
                continue
            an = _norm(apt)
            if not (an == cn or (cn and (cn in an or an in cn))):
                continue
            try:
                ar = float(it.findtext("excluUseAr"))
                if area and abs(ar - area) / area > 0.15:
                    continue
                amt = float(it.findtext("dealAmount").replace(",", "").strip())
                by = int(it.findtext("buildYear")) if (it.findtext("buildYear") or "").isdigit() else None
                jibun = (it.findtext("umdCd") or "", it.findtext("bonbun") or "", it.findtext("bubun") or "")
                d = (int(it.findtext("dealYear")), int(it.findtext("dealMonth")), int(it.findtext("dealDay")))
            except (ValueError, AttributeError, TypeError):
                continue
            if best is None or d > best[0]:
                best = (d, amt, by, jibun)
            # 단순 단지명 부분 일치·15% 면적 허용은 기존 참고가격에만 적용한다.
            # 입찰 엔진의 검증 표본에는 정확한 단지명과 전용면적을 요구한다.
            if area > 0 and an == cn and abs(ar - area) <= 0.1:
                try:
                    sold_at = f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}"
                    floor = int(it.findtext("floor")) if it.findtext("floor") else None
                except (ValueError, TypeError):
                    continue
                samples.append({"source": "molit", "price": amt, "exclusive_m2": ar,
                                "sold_at": sold_at, "floor": floor})
    result = (best[1], best[2], best[3]) if best else (None, None, None)
    if with_samples:
        unique = {(x["sold_at"], x["price"], x["floor"]): x for x in samples}
        return (*result, sorted(unique.values(), key=lambda x: x["sold_at"], reverse=True))
    return result


def recent_jeonse_price(lawd5: str, dong: str, core: str, area: float, key: str) -> float | None:
    """국토부 전월세 실거래에서 동일단지 최근 전세(월세=0) 보증금(만원). API 미활성 시 None."""
    base = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
    best, cn = None, _norm(core)
    for ym in _recent_yms(6):
        url = f"{base}?serviceKey={key}&LAWD_CD={lawd5}&DEAL_YMD={ym}&numOfRows=900&pageNo=1"
        try:
            root = ET.fromstring(urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
        except Exception:
            continue
        for it in root.iter("item"):
            if (it.findtext("monthlyRent") or "0").replace(",", "").strip() not in ("0", ""):
                continue  # 전세만 (월세 0)
            umd = (it.findtext("umdNm") or "").strip()
            if dong and umd and dong not in umd and umd not in dong:
                continue
            an = _norm(it.findtext("aptNm") or "")
            if not (an == cn or (cn and (cn in an or an in cn))):
                continue
            try:
                ar = float(it.findtext("excluUseAr"))
                if area and abs(ar - area) / area > 0.15:
                    continue
                dep = float((it.findtext("deposit") or "").replace(",", "").strip())
                d = (int(it.findtext("dealYear")), int(it.findtext("dealMonth")), int(it.findtext("dealDay")))
            except (ValueError, AttributeError, TypeError):
                continue
            if best is None or d > best[0]:
                best = (d, dep)
    return best[1] if best else None


def update_market(codes: dict, key: str, listing_id: str | None = None) -> int:
    """등록 매물의 최근 실거래가 + 건축물대장(용적률·세대수·연식)을 국토부에서 조회해 채운다."""
    from realty_signal.ingest.building import fetch_building
    from realty_signal.ingest.complex_grade import grade_in_region, region_grades

    listings = load()
    n = 0
    grade_cache: dict[str, list] = {}  # 시군구별 단지 급지 랭킹 (실거래 호출 재사용)
    for lst in listings:
        if listing_id is not None and lst.id != listing_id:
            continue
        code = codes.get(lst.region, "")
        if not (code and code.isdigit()):
            continue
        dong_m = re.search(r"([가-힣]+동)", lst.메모 or "")
        dong = dong_m.group(1) if dong_m else ""
        # 괄호 안 별칭(예: '(별내포스코더샵)')도 매칭에 쓰이도록 전체 단지명 사용
        price, build_year, jibun, samples = recent_trade_price(
            code[:5], dong, lst.단지명, lst.전용면적, key, with_samples=True)
        if samples:
            lst.실거래표본 = samples
            from realty_signal.time_kst import today_kst
            lst.실거래표본갱신일 = today_kst().isoformat()
        if price:
            lst.최근실거래가 = price
            if build_year:
                lst.건축년도 = build_year
            n += 1
        if listing_id is not None:
            continue  # 상세의 즉시 갱신은 입찰 엔진 표본만; 대장·급지·전세 조회를 기다리지 않는다
        # 실거래로 찾은 지번 → 건축물대장(용적률·건폐율·세대수·연식·층)
        if jibun and jibun[1]:
            b = fetch_building(code[:5], jibun[0], jibun[1], jibun[2], key)
            if b:
                lst.용적률 = b["용적률"] if b["용적률"] is not None else lst.용적률
                lst.건폐율 = b["건폐율"] if b["건폐율"] is not None else lst.건폐율
                lst.세대수 = b["세대수"] or lst.세대수
                lst.최고층 = b["최고층"] or lst.최고층
                if b["사용승인일"] and not lst.건축년도:  # 실거래 건축년도 없을 때 보강
                    lst.건축년도 = int(b["사용승인일"][:4])
        # 단지단위 급지 (지역 내 평단가 순위) — 시군구별 실거래 분포 1회만 호출
        lawd5 = code[:5]
        if lawd5 not in grade_cache:
            grade_cache[lawd5] = region_grades(lawd5, key)
        g = grade_in_region(lst.단지명, grade_cache[lawd5])
        if g:
            lst.단지평단가, lst.단지급지 = g["평단가"], g["급지"]
            lst.급지상위, lst.지역중앙대비 = g["상위"], g["중앙대비"]
        jeonse = recent_jeonse_price(code[:5], dong, lst.단지명, lst.전용면적, key)  # 전월세 API 활성 시
        if jeonse:
            lst.최근전세가 = jeonse
    save(listings)
    return n


def import_csv(text: str) -> int:
    n = 0
    for row in csv.DictReader(io.StringIO(text)):
        clean = {k: v for k, v in row.items() if k in Listing.__dataclass_fields__ and v not in ("", None)}
        for num in ("감정가", "최저매각가", "유찰횟수", "시세", "전용면적", "대출비율",
                    "월임대료", "임대보증금", "매도가", "미납관리비", "수리비"):
            if num in clean:
                try:
                    clean[num] = float(str(clean[num]).replace(",", ""))
                except ValueError:
                    clean.pop(num)
        if clean:
            add(clean)
            n += 1
    return n


# --- 우선순위 / 전략 ---
_SIG_WEIGHT = {"STRONG_BUY": 2, "BUY": 1}


def enrich(listings: list[Listing], signals: dict[str, str], overrides: dict | None = None) -> list[dict]:
    """탐색 목록에는 목적·현금·세금이 없는 옛 단순 상한을 노출하지 않는다."""
    out = []
    for lst in listings:
        sig = signals.get(lst.region, "HELD")
        score = _SIG_WEIGHT.get(sig, 0) * 10 - 100
        out.append({
            **asdict(lst), "지역시그널": sig, "권장입찰가": None,
            "예상낙찰가": None, "총비용우위": None, "총비용우위율": None,
            "시세차익": None, "시세차익률": None,
            "임대수익률": None, "매도수익률": None,
            "최저매각가": lst.최저매각가,
            "우선순위점수": round(score, 1),
            "목표달성": False,
            "입찰상태": "needs_review",
            "확인할것": ["목적별 비용·현금·권리·시세를 입찰 시뮬레이터에서 확인하세요."],
        })
    out.sort(key=lambda r: r["우선순위점수"], reverse=True)
    return out
