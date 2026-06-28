"""경매 매물 관리 + 입찰가 계산 + 우선순위/전략.

매물은 수동입력/CSV 로 받아 JSON(data/cache/auction.json)에 저장한다.
입찰가 공식은 _bid_calc 한 곳에 모아 두었으며, 사용자 고유 공식으로 교체하기 쉽다.

⚠️ 현재 공식은 합리적 기본값(목표수익률·예상낙찰가율·저감률 조정 가능)이며,
   사용자 확정 공식으로 교체 예정.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

AUCTION_FILE = Path("data/cache/auction.json")

# 입찰가 계산 기본 파라미터 (조정 가능)
DEFAULT_TARGET_RETURN = 0.12   # 목표 수익률 12%
DEFAULT_WIN_RATE = 0.85        # 예상 낙찰가율 (시세 대비)
DEFAULT_DROP_METRO = 0.20      # 수도권 유찰 1회당 저감률
DEFAULT_DROP_LOCAL = 0.30      # 지방 유찰 1회당 저감률

# CSV 헤더 → 필드
CSV_FIELDS = ["사건번호", "단지명", "region", "감정가", "최저매각가",
              "유찰횟수", "입찰기일", "시세", "예상비용", "면적", "메모"]


@dataclass
class Listing:
    사건번호: str = ""
    단지명: str = ""
    region: str = ""            # BUY+ 시그널 지역명
    감정가: float = 0.0          # 만원
    최저매각가: float | None = None  # 만원 (없으면 감정가·유찰로 산정)
    유찰횟수: int = 0
    입찰기일: str = ""           # YYYY-MM-DD
    시세: float | None = None    # 만원 (없으면 감정가로 대체)
    예상비용: float = 0.0        # 만원 (명도·수리·세금 등)
    면적: str = ""
    메모: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


def _drop_rate(region: str) -> float:
    metro = ("서울", "경기", "인천", "수도권")
    return DEFAULT_DROP_METRO if any(region.startswith(m) for m in metro) else DEFAULT_DROP_LOCAL


def bid_calc(lst: Listing, target_return: float = DEFAULT_TARGET_RETURN,
             win_rate: float = DEFAULT_WIN_RATE) -> dict:
    """입찰가 산정. (공식 교체 지점)"""
    appraisal = lst.감정가 or 0.0
    market = lst.시세 or appraisal           # 시세 없으면 감정가로 대체
    drop = _drop_rate(lst.region)
    min_bid = lst.최저매각가
    if min_bid is None:
        min_bid = round(appraisal * (1 - drop) ** max(lst.유찰횟수, 0))

    expected_win = round(market * win_rate)          # 예상 낙찰가
    my_cap = round(market * (1 - target_return) - lst.예상비용)  # 목표수익 상한
    # 권장 입찰가: 최저가 이상 + 상한 이내에서 예상낙찰가 근처
    recommend = min(my_cap, max(min_bid, expected_win))
    margin = round((market - recommend - lst.예상비용) / market * 100, 1) if market else 0.0
    profitable = my_cap >= min_bid

    return {
        "최저매각가": min_bid,
        "예상낙찰가": expected_win,
        "나의입찰상한": my_cap,
        "권장입찰가": recommend,
        "안전마진%": margin,
        "수익가능": profitable,
    }


# --- 저장소 (JSON) ---
def load() -> list[Listing]:
    if not AUCTION_FILE.exists():
        return []
    return [Listing(**d) for d in json.loads(AUCTION_FILE.read_text(encoding="utf-8"))]


def save(listings: list[Listing]) -> None:
    AUCTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUCTION_FILE.write_text(
        json.dumps([asdict(x) for x in listings], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add(data: dict) -> Listing:
    listings = load()
    lst = Listing(**{k: v for k, v in data.items() if k in Listing.__dataclass_fields__})
    listings.append(lst)
    save(listings)
    return lst


def remove(listing_id: str) -> None:
    save([x for x in load() if x.id != listing_id])


def import_csv(text: str) -> int:
    """CSV(헤더 포함) → 매물 추가. 추가 건수 반환."""
    import csv
    import io

    n = 0
    for row in csv.DictReader(io.StringIO(text)):
        clean = {k: v for k, v in row.items() if k in Listing.__dataclass_fields__ and v not in ("", None)}
        for num in ("감정가", "최저매각가", "유찰횟수", "시세", "예상비용"):
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


def enrich(listings: list[Listing], signals: dict[str, str],
           target_return: float = DEFAULT_TARGET_RETURN,
           win_rate: float = DEFAULT_WIN_RATE) -> list[dict]:
    """매물 + 계산 + 지역 시그널 + 우선순위 점수."""
    out = []
    for lst in listings:
        calc = bid_calc(lst, target_return, win_rate)
        sig = signals.get(lst.region, "")
        # 우선순위 = 신호가중*10 + 안전마진 (수익불가면 강한 감점)
        score = _SIG_WEIGHT.get(sig, 0) * 10 + calc["안전마진%"]
        if not calc["수익가능"]:
            score -= 100
        out.append({**asdict(lst), **calc, "지역시그널": sig, "우선순위점수": round(score, 1)})
    out.sort(key=lambda r: r["우선순위점수"], reverse=True)
    return out
