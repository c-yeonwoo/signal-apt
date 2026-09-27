"""사용자 입력 호가와 동일 단지·전용면적의 관측 거래분포 비교.

호가는 사용자가 입력한 별도 사실이며 국토부 비교거래 표본에 합치지 않는다.
가격 차이는 거래 조건·층·집 상태가 다른 관측값과의 산술 차이일 뿐 할인율/적정가가 아니다.
"""

from __future__ import annotations

from datetime import date
from math import isfinite


def validate_input(asking: float, exclusive_m2: float) -> tuple[float, float]:
    """원천 조회 전에 잘못된 입력을 거른다."""
    try:
        asking, exclusive_m2 = float(asking), float(exclusive_m2)
    except (TypeError, ValueError):
        raise ValueError("호가와 전용면적을 숫자로 입력해 주세요") from None
    if not all(isfinite(v) and v > 0 for v in (asking, exclusive_m2)):
        raise ValueError("호가와 전용면적은 0보다 큰 유한한 숫자여야 합니다")
    return asking, exclusive_m2


def assess(detail: dict, *, asking: float, exclusive_m2: float) -> dict:
    """표본 3건 미만·오래된/불명확한 근거는 비교 수치 없이 보류한다."""
    asking, exclusive_m2 = validate_input(asking, exclusive_m2)

    base = {"입력호가": round(asking), "전용㎡": round(exclusive_m2, 1),
            "source_id": "molit_aggregate", "price_kind": "user_entered_asking",
            "비교기준": "동일 단지·전용면적 0.1㎡·최근 6개월 신고거래; 층·상태·시점 미보정",
            "안내": ["호가는 사용자가 입력한 값이며 공공 실거래에 섞지 않습니다.",
                   "거래가격 차이는 할인율·적정가·실투자금·매수 권고가 아닙니다."]}

    def hold(reason: str) -> dict:
        return {**base, "상태": "보류", "이유": reason, "중앙값": None,
                "호가차액": None, "호가차이율": None, "표본수": 0}

    if detail.get("status") in {"failed", "ambiguous", "stale"} or detail.get("degraded"):
        return hold("단지 실거래 원천이 지연되거나 식별이 불명확합니다.")
    if detail.get("identity_status") != "single_observed":
        return hold("단지 주소·식별자를 확인하기 전에는 가격을 연결하지 않습니다.")
    row = next((p for p in detail.get("평형별") or []
                if p.get("전용㎡") is not None and round(float(p["전용㎡"]), 1) == round(exclusive_m2, 1)), None)
    if row is None:
        return hold("같은 전용면적의 거래 표본이 없습니다.")
    comp = row.get("비교거래") or {}
    if comp.get("상태") != "관측" or (comp.get("건수") or 0) < 3:
        return {**hold("최근 6개월 비교거래가 3건 미만이거나 거래유형이 확인되지 않습니다."),
                "표본수": comp.get("건수") or 0}
    try:
        asof = date.fromisoformat(comp["기준일"])
    except (KeyError, TypeError, ValueError):
        return hold("비교거래의 산정 기준일을 확인할 수 없습니다.")
    if not 0 <= (date.today() - asof).days <= 7:
        return hold("비교거래 근거가 갱신되지 않았습니다.")
    median = comp.get("중앙값")
    if not isinstance(median, (int, float)) or not isfinite(median) or median <= 0:
        return hold("비교거래 중앙값이 유효하지 않습니다.")
    diff = asking - median
    return {**base, "상태": "관측비교", "이유": None,
            "중앙값": round(median), "범위": [comp.get("최저"), comp.get("최고")],
            "표본수": comp["건수"], "거래월범위": comp.get("거래월범위"),
            "호가차액": round(diff), "호가차이율": round(diff / median * 100, 1),
            "층미통제": True, "거래유형미상건수": comp.get("거래유형미상건수") or 0,
            "비교기준일": comp["기준일"]}
