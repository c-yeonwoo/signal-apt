"""Versioned evidence/decision envelope shared by UI and AI read models.

This never upgrades an engine's provisional result to bank approval. Missing
source timestamps, dwelling identity and legal verification remain explicit.
"""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json

from realty_signal import buying_power, regulation

VERSION = "buyer-decision-v1"


def cache_payload(value):
    """Read-model generation time/cache hit flags must not invalidate AI reuse."""
    if isinstance(value, dict):
        return {k: cache_payload(v) for k, v in value.items() if k not in {"generated_at", "cached"}}
    if isinstance(value, list):
        return [cache_payload(v) for v in value]
    return value


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def finance_fingerprint(params):
    return fingerprint({"params": asdict(params), "engine": buying_power.MODEL_VERSION,
                        "policy": regulation.policy_manifest()["version"],
                        "defaults": buying_power.DEFAULTS, "broker": buying_power.BROKER_BRACKETS,
                        "legal": buying_power.LEGAL_BRACKETS, "stamp": buying_power.STAMP_BRACKETS})


def build(row, params, *, uid=None):
    finance = row.get("자금") or {}
    kind = row.get("유형") or "단지"
    region = row.get("지역") or row.get("region")
    explicit = row.get("price_kind")
    if explicit == "asking" and kind in ("급매", "찐매물", "일반매물"):
        price_kind = "asking"
    elif row.get("가격출처") == "매물가" and kind in ("급매", "찐매물", "일반매물"):
        price_kind = "asking"
    else:
        price_kind = "modeled"
    source = row.get("source") or ("molit_aggregate" if kind == "단지" else "unknown")
    price = row.get("추정가", row.get("예상가", row.get("총액")))
    evidence = {"source_id": source, "source_record_id": row.get("ref"),
                "entity_id": fingerprint([region, row.get("단지명") or row.get("단지"), row.get("ref")]),
                "metric": "price", "value": price, "unit": "만원", "price_kind": price_kind,
                "observed_at": row.get("observed_at"), "published_at": row.get("published_at"),
                "fetched_at": row.get("fetched_at"), "valid_until": row.get("valid_until"),
                "spatial_grain": "listing" if price_kind == "asking" else "complex_estimate",
                "sample_n": row.get("sample_n"), "version": VERSION,
                "status": "stale" if row.get("stale") else "partial" if row.get("degraded") else "observed" if row.get("fetched_at") else "unverified_timestamp"}
    evidence["id"] = fingerprint(evidence)
    unknowns = ["은행 심사·권리·입주 조건", "규제·세율 최신 적용 확인"]
    if price_kind != "asking":
        unknowns.append("현재 호가·동호수 확인")
    if evidence["status"] != "observed":
        unknowns.append("원천 자료 시점·신선도 확인")
    if not row.get("area") and not row.get("전용면적"):
        unknowns.append("전용면적·세금 가정 확인")
    if not row.get("통근"):
        unknowns.append("실제 출퇴근 경로 확인")
    auction_state = row.get("입찰상태") if kind == "경매" else None
    if kind == "경매" and auction_state != "conditional_bid":
        unknowns.append("경매 입찰가·권리·시세 근거 확인")
    finance_blocked = bool(finance and not finance.get("가능") and not finance.get("확인필요"))
    auction_blocked = auction_state == "no_bid"
    blocked = finance_blocked or auction_blocked
    reasons = (["입력한 자금 또는 월 부담 한도 초과"] if finance_blocked else [])
    if auction_blocked:
        reasons.append("목표 총비용 우위율을 충족하는 입찰가 없음")
    unknown = (row.get("예산확인필요", price_kind != "asking") or finance.get("확인필요")
               or not finance or price is None or evidence["status"] != "observed"
               or (kind == "경매" and auction_state != "conditional_bid"))
    feasibility = "infeasible" if blocked else "unknown" if unknown else "conditional"
    decision = {"version": VERSION, "profile_version": fingerprint([uid, finance_fingerprint(params)]),
                "policy_version": regulation.policy_manifest()["version"],
                "evidence_ids": [evidence["id"]], "feasibility": feasibility,
                "blocking_reasons": reasons, "unknowns": unknowns,
                "preference_breakdown": row.get("분해") or {"budget_headroom": row.get("_score")},
                "next_action": "이 물건 입찰 보류" if auction_blocked else
                               "가격·자금 조건 다시 설정" if finance_blocked else
                               "권리·시세 근거 확인 후 입찰 검토" if kind == "경매" and auction_state != "conditional_bid" else
                               "호가·전용면적과 은행 한도 확인",
                "scope": "가정 기반 비교이며 구매 가능 확정이 아님"}
    decision["id"] = fingerprint(decision)
    decision["generated_at"] = datetime.now(timezone.utc).isoformat()
    return {"decision": decision, "evidence": [evidence], "lines": card_lines(row, decision)}


def _eok(man) -> str:
    if man is None or man <= 0:
        return "–"
    return f"{float(man) / 10000:.1f}억"


def card_lines(row: dict, decision: dict | None) -> dict:
    """현금·가격·미확인·다음. 화면은 이 네 문장만 그린다."""
    finance = row.get("자금") or {}
    kind = row.get("유형") or ""
    src = row.get("가격출처") or ""
    modeled = src in ("지역평단추정", "단지평단추정")
    auction_state = row.get("입찰상태") if kind == "경매" else None
    decision = decision or {}
    if kind == "경매" and auction_state and auction_state != "conditional_bid":
        cash = "아직 입찰하지 않습니다. 필요한 돈은 계산 전입니다"
    elif finance.get("필요현금") is not None:
        month = finance.get("총월상환", finance.get("월상환"))
        if finance.get("확인필요"):
            state = "확인이 필요합니다"
        elif finance.get("가능") is False or decision.get("feasibility") == "infeasible":
            state = "가진 돈으로 안 됩니다"
        elif modeled:
            state = "짐작한 가격입니다. 매물 가격은 모릅니다"
        elif decision.get("feasibility") == "unknown":
            state = "확인이 필요합니다"
        else:
            state = "계산상 됩니다"
        month_bit = f" · 매달 {int(month):,}만" if isinstance(month, (int, float)) else ""
        cash = f"필요한 돈 {_eok(finance.get('필요현금'))}{month_bit} · {state}"
    elif row.get("예산내"):
        cash = "예산 안 가격입니다. 세금과 중개비는 따로입니다"
    elif row.get("총액") or row.get("추정가") or row.get("예상가"):
        cash = "살 수 있는 가격을 저장하면 필요한 돈을 계산합니다"
    else:
        cash = "가격이 없어 필요한 돈을 계산하지 못했습니다"
    if kind == "경매":
        price = "최저 입찰가입니다. 세금과 비용은 따로입니다"
    elif src == "매물가":
        price = "매물에 적힌 가격입니다. 아직 팔리는지는 모릅니다"
    elif modeled:
        price = "동네 평균으로 짐작한 가격입니다. 매물 가격이 아닙니다"
    elif src == "사용자입력":
        price = "직접 넣은 가격입니다. 매물에 적힌 가격이 아닙니다"
    elif row.get("추정가") or row.get("총액") or row.get("예상가"):
        price = "이 가격이 어디서 온 것인지 확인이 필요합니다"
    else:
        price = "가격을 모릅니다"
    unknowns = decision.get("unknowns") or []
    blocking = decision.get("blocking_reasons") or []
    unknown = " · ".join(unknowns[:2]) if unknowns else (blocking[0] if blocking else "매물 가격, 권리, 은행 대출")
    nxt = decision.get("next_action") or (
        "권리와 시세를 확인한 뒤 입찰을 검토하세요" if kind == "경매" and auction_state and auction_state != "conditional_bid"
        else "매물 가격과 필요한 돈을 확인하세요")
    return {"cash": cash, "price": price, "unknown": unknown, "next": nxt}


def annotate(row: dict, params, *, uid=None, sido_of=None) -> dict:
    """가격이 있고 입찰 보류가 아니면 지역 규제 기준으로 자금을 붙이고 네 줄을 만든다."""
    out = dict(row)
    price = out.get("추정가", out.get("예상가", out.get("총액")))
    kind = out.get("유형") or ""
    auction_state = out.get("입찰상태")
    skip = kind == "경매" and auction_state and auction_state != "conditional_bid"
    used = params or buying_power.Params(capital=0)
    if (params is not None and params.capital > 0 and price and not skip and not out.get("자금")):
        region = out.get("지역") or out.get("region")
        sido = sido_of(region) if sido_of else None
        used = buying_power.params_for_region(params, region, sido)
        try:
            out["자금"] = buying_power.for_price(float(price), used)
        except (TypeError, ValueError):
            used = params
    out.update(build(out, used, uid=uid))
    return out
