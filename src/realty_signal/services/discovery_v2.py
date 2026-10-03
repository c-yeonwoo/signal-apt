"""Deterministic listing discovery with explicit pass, unknown and fail tiers."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from realty_signal.services.property_analysis import snapshot

VERSION = "discovery-v2-1"
KINDS = {"일반매물", "급매", "찐매물"}
FIELDS = {"max_price_manwon", "min_area_m2", "max_monthly_manwon", "region", "prefer_region"}


def _number(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if isfinite(v) and v > 0 else None


def validate(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("invalid_conditions")
    unknown = set(spec) - FIELDS - {"limit", "include_exceeded"}
    if unknown:
        raise ValueError("unsupported_condition")
    cleaned = {}
    for key in ("max_price_manwon", "min_area_m2", "max_monthly_manwon"):
        if spec.get(key) is not None:
            value = _number(spec[key])
            if value is None or value > 5_000_000:
                raise ValueError("invalid_condition_value")
            cleaned[key] = value
    if spec.get("region") is not None:
        if not isinstance(spec["region"], str) or len(spec["region"]) > 80:
            raise ValueError("invalid_region")
        cleaned["region"] = spec["region"].strip()
    if spec.get("prefer_region") is not None:
        if not isinstance(spec["prefer_region"], str) or len(spec["prefer_region"]) > 80:
            raise ValueError("invalid_region")
        cleaned["prefer_region"] = spec["prefer_region"].strip()
    if cleaned.get("region") and cleaned.get("prefer_region"):
        raise ValueError("conflicting_region_conditions")
    limit = spec.get("limit", 12)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("invalid_limit")
    cleaned["limit"] = limit
    cleaned["include_exceeded"] = spec.get("include_exceeded") is True
    return cleaned


def _monthly(row: dict) -> float | None:
    finance = row.get("자금") or {}
    return _number(finance.get("총월상환") or finance.get("월상환"))


def classify(row: dict, spec: dict) -> dict:
    listing = snapshot(row)
    price = listing["asking_manwon"]
    area = listing["exclusive_m2"]
    checks = []
    if "max_price_manwon" in spec:
        checks.append({"field": "max_price_manwon", "status": "unknown" if price is None or listing["stale"]
                       else "pass" if price <= spec["max_price_manwon"] else "fail",
                       "value": price, "limit": spec["max_price_manwon"]})
    if "min_area_m2" in spec:
        checks.append({"field": "min_area_m2", "status": "unknown" if area is None
                       else "pass" if area >= spec["min_area_m2"] else "fail",
                       "value": area, "limit": spec["min_area_m2"]})
    if "max_monthly_manwon" in spec:
        monthly = _monthly(row)
        checks.append({"field": "max_monthly_manwon", "status": "unknown" if monthly is None
                       else "pass" if monthly <= spec["max_monthly_manwon"] else "fail",
                       "value": monthly, "limit": spec["max_monthly_manwon"]})
    if "region" in spec and spec["region"]:
        checks.append({"field": "region", "status": "pass" if listing["region"] == spec["region"] else "fail",
                       "value": listing["region"], "limit": spec["region"]})
    preferred = spec.get("prefer_region")
    preferred_known = bool(listing["region"])
    preference_match = bool(preferred and preferred_known and listing["region"] == preferred)
    preference_score = (100 if preference_match else 0) if preferred else None
    preference_coverage = (100 if preferred_known else 0) if preferred else None
    tier = "exceeded" if any(x["status"] == "fail" for x in checks) else (
        "verify" if any(x["status"] == "unknown" for x in checks) or listing["stale"] or
        listing["collected_at"] is None else "matched" if checks else "explore")
    passed = [x for x in checks if x["status"] == "pass"]
    missing = [x for x in checks if x["status"] == "unknown"]
    failed = [x for x in checks if x["status"] == "fail"]
    if passed:
        reason = {"max_price_manwon": "설정한 호가 상한 안입니다.",
                  "min_area_m2": "원하는 전용면적 이상입니다.",
                  "max_monthly_manwon": "계산한 월 부담이 설정 상한 안입니다.",
                  "region": "선택한 지역입니다."}[passed[0]["field"]]
    else:
        reason = "조건을 입력하면 적합성을 비교합니다." if not checks else "조건 충족을 확인하지 못했습니다."
    if preference_match:
        reason = "선호 지역과 " + reason if passed else "선호 지역입니다."
    tradeoff = ("현재 알려진 조건에서는 양보할 점을 확인하지 못했습니다." if not failed else
                f"{failed[0]['field']} 조건을 넘습니다.")
    verify = ("매물 판매 여부와 실제 호가를 확인하세요." if listing["stale"] else
              f"{missing[0]['field']} 자료를 확인하세요." if missing else
              "실제 자금·매물 상태를 확인하세요.")
    return {"listing": listing, "eligibility": tier, "constraints": checks,
            "recommendation_reason": reason, "tradeoff": tradeoff,
            "verify_next": verify, "signal_context": row.get("시그널"),
            "preference": {"region": preferred, "matched": preference_match,
                           "score": preference_score, "coverage": preference_coverage},
            "rank_version": VERSION}


def discover(rows: list[dict], spec: dict) -> dict:
    spec = validate(spec)
    seen = set()
    groups = {"matched": [], "verify": [], "exceeded": [], "explore": []}
    for row in rows:
        if row.get("유형") not in KINDS or not row.get("key") or row["key"] in seen:
            continue
        seen.add(row["key"])
        item = classify(row, spec)
        groups[item["eligibility"]].append(item)
    def order(item):
        snap = item["listing"]
        return (-(item["preference"]["score"] or 0),
                -sum(x["status"] == "pass" for x in item["constraints"]),
                snap["asking_manwon"] if snap["asking_manwon"] is not None else float("inf"),
                snap["key"])
    for items in groups.values():
        items.sort(key=order)
        # Keep the first page useful when one complex has many individual listings.
        selected, overflow, complex_counts = [], [], {}
        for item in items:
            listing = item["listing"]
            complex_key = (listing["region"], listing["name"])
            if complex_counts.get(complex_key, 0) < 2:
                selected.append(item)
                complex_counts[complex_key] = complex_counts.get(complex_key, 0) + 1
            else:
                overflow.append(item)
        items[:] = selected + overflow
    counts = {k: len(v) for k, v in groups.items()}
    if not spec["include_exceeded"]:
        groups["exceeded"] = []
    limit = spec["limit"]
    return {"version": VERSION, "groups": {k: v[:limit] for k, v in groups.items()},
            "counts": counts,
            "basis": "수집된 일반·급매·찐매물의 호가와 확인 가능한 조건만 비교합니다. 구매 가능 확정이 아닙니다.",
            "generated_at": datetime.now(timezone.utc).isoformat()}
