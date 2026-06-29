"""경매 매물 관리 + 입찰가 산정 + 우선순위/전략.

입찰가 계산은 경매 입찰가 산정표 모델이다:
  - 경매 총매입비용 = 입찰가 + 등기비 + 명도비 + 미납관리비 + 수리비 + 대리입찰 + 인수보증금 + 보유이자
  - 일반매매 총매입비용 = 시세 + 취득세 + 중개수수료 + 법무비
  - 시세차익 = 일반매매총매입 − 경매총매입,  시세차익률 = 시세차익 / 경매총매입
  - 실투자금 = 경매총매입 − 대출금(=입찰가×대출비율)
  - 임대수익률 = (월세×12 − 대출금×금리) / (실투자금 − 임대보증금)
  - 단기매도 순수익 = 매도가 − 경매총매입 − 매도중개보수
낙찰가율(감정가 대비)을 1%씩 변화시킨 민감도 표를 만들고,
목표 시세차익률을 만족하는 '권장 입찰가'를 도출한다.

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
    "목표시세차익률": 0.10,   # 권장 입찰가 산정 기준
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
    인수보증금: float = 0.0
    대리입찰비: float = 0.0
    최근실거래가: float | None = None   # 국토부 동일단지 최근 매매(만원)
    최근전세가: float | None = None     # 국토부 동일단지 최근 전세(만원)
    건축년도: int | None = None         # 국토부 실거래 매칭 단지 건축년도
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
    market = lst.시세 or lst.최근실거래가 or lst.감정가  # 시세 미입력 시 최근 실거래가 → 감정가 순

    등기비 = 입찰가 * p["취득세율"] + p["법무비"] / 10000  # 법무비는 원→만원
    명도비 = lst.전용면적 * p["㎡_평"] * p["명도_평당"] / 10000
    부대 = 등기비 + 명도비 + lst.미납관리비 + lst.수리비 + lst.대리입찰비 + lst.인수보증금
    대출금 = 입찰가 * loan_ratio
    보유이자 = 대출금 * rate / 12 * p["보유개월"]
    경매총매입 = 입찰가 + 부대 + 보유이자

    매매총매입 = market * (1 + p["취득세율"] + p["매수중개율"]) + p["법무비"] / 10000
    시세차익 = 매매총매입 - 경매총매입
    시세차익률 = 시세차익 / 경매총매입 if 경매총매입 else 0.0
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
        "매매총매입": rnd(매매총매입), "시세차익": rnd(시세차익), "시세차익률": pct(시세차익률),
        "실투자금": rnd(실투자금), "임대수익률": pct(임대수익률),
        "매도순수익": rnd(매도순수익), "매도수익률": pct(매도수익률),
    }


def _floor_rate(lst: Listing) -> float:
    if lst.감정가 and lst.최저매각가:
        return lst.최저매각가 / lst.감정가
    return 0.7  # 최저가 미상 시 70% 가정


def table(lst: Listing, p: dict, span: float = 0.30, step: float = 0.01) -> list[dict]:
    """낙찰가율(감정가 대비)별 민감도 표 (낮은 입찰가→높은 입찰가)."""
    floor = _floor_rate(lst)
    rows = []
    r = floor
    while r <= min(1.0, floor + span) + 1e-9:
        bid = round(lst.감정가 * r)
        rows.append({"낙찰가율": round(r * 100, 1), **breakdown(lst, bid, p)})
        r += step
    return rows


def recommend(lst: Listing, p: dict) -> dict:
    """목표 시세차익률을 만족하는 최대 입찰가(권장)와 그 분해."""
    rows = table(lst, p)
    target = p["목표시세차익률"] * 100
    ok = [row for row in rows if row["시세차익률"] is not None and row["시세차익률"] >= target]
    chosen = max(ok, key=lambda x: x["입찰가"]) if ok else rows[0]
    return chosen


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


def _norm(s: str) -> str:
    # 한글·숫자만 남김 (공백·괄호·특수문자·'아파트' 표기차 제거)
    return re.sub(r"[^가-힣0-9]", "", (s or "").replace("아파트", ""))


def _recent_yms(n: int = 6) -> list[str]:
    from datetime import date

    y, m, out = date.today().year, date.today().month, []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out


def recent_trade_price(lawd5: str, dong: str, core: str, area: float, key: str) -> tuple[float, int] | tuple[None, None]:
    """국토부 매매 실거래에서 동일단지(동+이름+면적) 최근 (거래금액 만원, 건축년도)."""
    base = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    best = None
    cn = _norm(core)
    for ym in _recent_yms(6):
        url = f"{base}?serviceKey={key}&LAWD_CD={lawd5}&DEAL_YMD={ym}&numOfRows=600&pageNo=1"
        try:
            root = ET.fromstring(urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
        except Exception:
            continue
        for it in root.iter("item"):
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
                d = (int(it.findtext("dealYear")), int(it.findtext("dealMonth")), int(it.findtext("dealDay")))
            except (ValueError, AttributeError, TypeError):
                continue
            if best is None or d > best[0]:
                best = (d, amt, by)
    return (best[1], best[2]) if best else (None, None)


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


def update_market(codes: dict, key: str) -> int:
    """등록 매물의 최근 실거래가를 국토부에서 조회해 채운다. 갱신 건수 반환."""
    listings = load()
    n = 0
    for lst in listings:
        code = codes.get(lst.region, "")
        if not (code and code.isdigit()):
            continue
        dong_m = re.search(r"([가-힣]+동)", lst.메모 or "")
        dong = dong_m.group(1) if dong_m else ""
        # 괄호 안 별칭(예: '(별내포스코더샵)')도 매칭에 쓰이도록 전체 단지명 사용
        price, build_year = recent_trade_price(code[:5], dong, lst.단지명, lst.전용면적, key)
        if price:
            lst.최근실거래가 = price
            if build_year:
                lst.건축년도 = build_year
            n += 1
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
    """매물 + 권장입찰가/시세차익률 + 지역시그널 + 우선순위 점수."""
    p = _p(overrides)
    out = []
    for lst in listings:
        rec = recommend(lst, p)
        sig = signals.get(lst.region, "")
        margin = rec["시세차익률"] or 0.0
        score = _SIG_WEIGHT.get(sig, 0) * 10 + margin
        if margin < p["목표시세차익률"] * 100:
            score -= 100  # 목표 미달 매물은 후순위
        out.append({
            **asdict(lst), "지역시그널": sig, "권장입찰가": rec["입찰가"],
            "예상낙찰가": rec["입찰가"], "시세차익": rec["시세차익"], "시세차익률": rec["시세차익률"],
            "임대수익률": rec["임대수익률"], "매도수익률": rec["매도수익률"],
            "최저매각가": rec.get("최저매각가") or lst.최저매각가,
            "우선순위점수": round(score, 1),
            "목표달성": margin >= p["목표시세차익률"] * 100,
        })
    out.sort(key=lambda r: r["우선순위점수"], reverse=True)
    return out
