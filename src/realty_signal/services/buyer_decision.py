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
    price_kind = "asking" if row.get("가격출처") == "매물가" and kind in ("급매", "찐매물") else "modeled"
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
    blocked = bool(finance and not finance.get("가능") and not finance.get("확인필요"))
    reasons = ["입력한 자금 또는 월 부담 한도 초과"] if blocked else []
    unknown = (row.get("예산확인필요", price_kind != "asking") or finance.get("확인필요")
               or not finance or price is None or evidence["status"] != "observed")
    feasibility = "infeasible" if blocked else "unknown" if unknown else "conditional"
    decision = {"version": VERSION, "profile_version": fingerprint([uid, finance_fingerprint(params)]),
                "policy_version": regulation.policy_manifest()["version"],
                "evidence_ids": [evidence["id"]], "feasibility": feasibility,
                "blocking_reasons": reasons, "unknowns": unknowns,
                "preference_breakdown": row.get("분해") or {"budget_headroom": row.get("_score")},
                "next_action": "가격·자금 조건 다시 설정" if blocked else "호가·전용면적과 은행 한도 확인",
                "scope": "가정 기반 비교이며 구매 가능 확정이 아님"}
    decision["id"] = fingerprint(decision)
    decision["generated_at"] = datetime.now(timezone.utc).isoformat()
    return {"decision": decision, "evidence": [evidence]}
