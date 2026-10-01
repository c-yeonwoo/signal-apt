"""같은 자본의 네 갈래. 승자를 고르지 않는다.

갭 보증금과 재건축 분담금은 입력이 있을 때만 계산한다.
"""

from __future__ import annotations

from realty_signal import buying_power
from realty_signal.services.buyer_decision import card_lines


def compare(params: buying_power.Params, *, price: float | None = None,
            jeonse: float | None = None, contribution: float | None = None,
            auction_price: float | None = None, auction_state: str | None = None) -> dict:
    if params.capital <= 0:
        return {"ready": False, "reason": "no_capital", "winner": None, "columns": [],
                "message": "가용자본을 입력하면 같은 돈의 네 갈래를 계산합니다."}
    columns = [
        _live(params, price),
        _gap(params, price, jeonse),
        _auction(params, auction_price, auction_state),
        _rebuild(params, price, contribution),
    ]
    return {"ready": True, "winner": None, "price": price, "columns": columns}


def _live(params, price):
    if price is None:
        statement = buying_power.statement(params)
        ceiling = float(statement["최대매수가"])
        finance = buying_power.for_price(ceiling, params) if ceiling else None
        row = {"유형": "실거주", "가격출처": "사용자입력", "추정가": ceiling or None, "자금": finance}
        decision = {"feasibility": "unknown", "unknowns": ["개별 호가를 넣지 않았습니다"],
                    "next_action": "보고 싶은 매매가를 넣으면 그 집 기준으로 다시 계산합니다"}
        return _col("live", "실거주", decision, row, "확정 상한 기준 · 6개월 전입 의무는 규제지역만")
    finance = buying_power.for_price(price, params)
    feasible = "infeasible" if not finance["가능"] and not finance["확인필요"] else (
        "unknown" if finance["확인필요"] else "conditional")
    decision = {
        "feasibility": feasible,
        "unknowns": ["은행 심사·권리·입주 조건"],
        "blocking_reasons": ["입력한 자금 또는 월 부담 한도 초과"] if feasible == "infeasible" else [],
        "next_action": "가격·자금 조건 다시 설정" if feasible == "infeasible" else "호가·전용면적과 은행 한도 확인",
    }
    row = {"유형": "실거주", "가격출처": "사용자입력", "추정가": price, "자금": finance}
    return _col("live", "실거주", decision, row, "매매 잔금과 취득비용 · 규제지역이면 6개월 내 전입")


def _gap(params, price, jeonse):
    if jeonse is None or price is None:
        missing = "전세보증금" if jeonse is None else "매매가"
        decision = {"feasibility": "unknown", "unknowns": [f"{missing}이 없어 갭 현금을 계산하지 않았습니다"],
                    "next_action": "매매가와 전세보증금을 넣으면 필요현금만 계산합니다"}
        return _col("gap", "갭", decision, {"유형": "갭", "가격출처": "사용자입력"},
                    "보증금이 없으면 이 칸은 비어 있습니다")
    if jeonse >= price:
        decision = {"feasibility": "unknown", "unknowns": ["보증금이 매매가 이상입니다"],
                    "next_action": "매매가와 보증금을 다시 확인"}
        return _col("gap", "갭", decision, {"유형": "갭", "가격출처": "사용자입력", "추정가": price},
                    "전세 만기 때 보증금을 돌려줄 의무")
    cash = buying_power.cash_needed(price, params, 0)
    need = cash["합계"] - jeonse
    available = max(0.0, params.capital - params.reserve_cash)
    possible = need <= available + 1e-6
    finance = {"필요현금": round(need), "월상환": 0, "가능": possible, "확인필요": False}
    decision = {
        "feasibility": "infeasible" if not possible else "unknown",
        "unknowns": ["전세 만기 때 보증금 반환", "은행 심사·권리"],
        "blocking_reasons": ["입력한 자금 한도 초과"] if not possible else [],
        "next_action": "다른 매매가·보증금을 확인" if not possible else "보증금 반환 시점과 권리를 확인",
    }
    row = {"유형": "갭", "가격출처": "사용자입력", "추정가": price, "자금": finance}
    return _col("gap", "갭", decision, row, "필요현금은 매매가·취득비용에서 입력 보증금을 뺀 값")


def _auction(params, auction_price, auction_state):
    if auction_price is None or not auction_state:
        decision = {"feasibility": "unknown", "unknowns": ["조건부 입찰로 볼 물건이 이 응답에 없습니다"],
                    "next_action": "권리·시세 근거가 있는 물건만 이 칸에 올립니다"}
        return _col("auction", "경매", decision, {"유형": "경매"}, "입찰기일 전 권리 확인")
    row = {"유형": "경매", "입찰상태": auction_state, "가격출처": "사용자입력", "추정가": auction_price}
    if auction_state != "conditional_bid":
        decision = {"feasibility": "infeasible" if auction_state == "no_bid" else "unknown",
                    "unknowns": ["경매 입찰가·권리·시세 근거 확인"],
                    "blocking_reasons": ["목표 총비용 우위율을 충족하는 입찰가 없음"] if auction_state == "no_bid" else [],
                    "next_action": "이 물건 입찰 보류" if auction_state == "no_bid" else "권리·시세 근거 확인 후 입찰 검토"}
        return _col("auction", "경매", decision, row, "조건부 입찰이 아니면 필요현금을 산정하지 않습니다")
    finance = buying_power.for_price(auction_price, params)
    feasible = "infeasible" if not finance["가능"] and not finance["확인필요"] else "unknown"
    decision = {"feasibility": feasible, "unknowns": ["낙찰가 미확정", "권리 분석"],
                "blocking_reasons": ["입력한 자금 또는 월 부담 한도 초과"] if feasible == "infeasible" else [],
                "next_action": "최저매각가 기준으로 현금을 확인하고 낙찰가는 따로 봅니다"}
    row["자금"] = finance
    return _col("auction", "경매", decision, row, "최저매각가 기준 · 낙찰가 미확정")


def _rebuild(params, price, contribution):
    if contribution is None or price is None:
        missing = "분담금" if contribution is None else "매매가"
        decision = {"feasibility": "unknown", "unknowns": [f"{missing} 입력이 없습니다"],
                    "next_action": "분담금을 넣기 전에는 재건축 현금을 계산하지 않습니다"}
        return _col("rebuild", "재건축", decision, {"유형": "재건축", "가격출처": "사용자입력"},
                    "사업 기간과 분담금은 입력 전엔 비어 있습니다")
    finance = buying_power.for_price(price, params)
    need = finance["필요현금"] + contribution
    available = max(0.0, params.capital - params.reserve_cash)
    possible = finance["가능"] and not finance["확인필요"] and need <= available + 1e-6
    merged = dict(finance)
    merged["필요현금"] = round(need)
    merged["가능"] = possible
    feasible = "unknown" if finance["확인필요"] else "infeasible" if not possible else "unknown"
    decision = {
        "feasibility": feasible,
        "unknowns": ["입력한 분담금 · 사업 단계 미확인"],
        "blocking_reasons": ["입력한 자금 한도 초과"] if feasible == "infeasible" else [],
        "next_action": "분담금과 사업 단계를 공식 자료로 확인",
    }
    row = {"유형": "재건축", "가격출처": "사용자입력", "추정가": price, "자금": merged}
    return _col("rebuild", "재건축", decision, row, "필요현금은 현재 매수가 가정에 입력 분담금을 더한 값")


def _col(key, label, decision, row, when):
    return {"id": key, "label": label, "feasibility": decision["feasibility"],
            "시간": when, "lines": card_lines(row, decision)}
