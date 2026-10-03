"""Deterministic listing discovery with explicit pass, unknown and fail tiers."""

from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite

from realty_signal.services.property_analysis import snapshot

VERSION = "discovery-v2-5"
KINDS = {"일반매물", "급매", "찐매물"}
FIELDS = {"max_price_manwon", "min_area_m2", "max_monthly_manwon", "region", "prefer_region",
          "prefer_max_price_manwon", "prefer_min_area_m2", "priority"}
PRIORITIES = {"balanced", "region", "price", "area"}


def _number(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if isfinite(v) and v > 0 else None


def validate(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("invalid_conditions")
    unknown = set(spec) - FIELDS - {"limit", "include_exceeded", "cursor"}
    if unknown:
        raise ValueError("unsupported_condition")
    cleaned = {}
    for key in ("max_price_manwon", "min_area_m2", "max_monthly_manwon",
                "prefer_max_price_manwon", "prefer_min_area_m2"):
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
    priority = spec.get("priority", "balanced")
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise ValueError("invalid_priority")
    cleaned["priority"] = priority
    limit = spec.get("limit", 12)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("invalid_limit")
    cleaned["limit"] = limit
    cleaned["include_exceeded"] = spec.get("include_exceeded") is True
    if spec.get("cursor") is not None:
        cursor = spec["cursor"]
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024 or not cursor.isascii():
            raise ValueError("invalid_cursor")
        cleaned["cursor"] = cursor
    return cleaned


def _hash(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def _encode_cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> dict:
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"version", "query", "snapshot", "offset"}:
            raise ValueError("invalid_cursor")
        return payload
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid_cursor") from exc


def _monthly(finance: dict | None) -> float | None:
    value = (finance or {}).get("monthly_exact_manwon")
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        return None
    return float(value)


def _preference(listing: dict, spec: dict) -> dict:
    """One explicit priority doubles its weight; unknowns remain in the denominator."""
    details = []
    definitions = (("region", "선호 지역", spec.get("prefer_region"), listing["region"],
                    bool(listing["region"]), lambda v, target: v == target),
                   ("price", "선호 호가", spec.get("prefer_max_price_manwon"),
                    listing["asking_manwon"], listing["asking_manwon"] is not None and not listing["stale"],
                    lambda v, target: v <= target),
                   ("area", "선호 면적", spec.get("prefer_min_area_m2"),
                    listing["exclusive_m2"], listing["exclusive_m2"] is not None and not listing["stale"],
                    lambda v, target: v >= target))
    for field, label, target, value, known, meets in definitions:
        if target is None or target == "":
            continue
        weight = 2 if field == spec.get("priority") else 1
        details.append({"field": field, "label": label, "status": "unknown" if not known else
                        "matched" if meets(value, target) else "missed", "value": value,
                        "target": target, "weight": weight})
    total = len(details)
    known = sum(x["status"] != "unknown" for x in details)
    satisfied = sum(x["status"] == "matched" for x in details)
    weight_total = sum(x["weight"] for x in details)
    weight_known = sum(x["weight"] for x in details if x["status"] != "unknown")
    weight_satisfied = sum(x["weight"] for x in details if x["status"] == "matched")
    applied_priority = spec.get("priority") if any(x["field"] == spec.get("priority") for x in details) else "balanced"
    return {"region": spec.get("prefer_region"),
            "matched": any(x["field"] == "region" and x["status"] == "matched" for x in details),
            "score": round(100 * weight_satisfied / weight_total) if weight_total else None,
            "coverage": round(100 * weight_known / weight_total) if weight_total else None,
            "satisfied": satisfied, "known": known, "total": total, "details": details,
            "priority": applied_priority, "weight_total": weight_total,
            "weight_satisfied": weight_satisfied, "weight_known": weight_known}


def _freshness(listing: dict) -> int:
    try:
        return date.fromisoformat((listing.get("collected_at") or "")[:10]).toordinal()
    except ValueError:
        return 0


def classify(row: dict, spec: dict, *, finance: dict | None = None) -> dict:
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
        monthly = _monthly(finance)
        assessed = (finance or {}).get("status") == "assessed" and monthly is not None
        checks.append({"field": "max_monthly_manwon", "status": "unknown" if not assessed
                       else "pass" if monthly <= spec["max_monthly_manwon"] and finance.get("possible") is True
                       else "fail",
                       "value": monthly, "limit": spec["max_monthly_manwon"]})
    if "region" in spec and spec["region"]:
        checks.append({"field": "region", "status": "pass" if listing["region"] == spec["region"] else "fail",
                       "value": listing["region"], "limit": spec["region"]})
    preference = _preference(listing, spec)
    tier = "exceeded" if any(x["status"] == "fail" for x in checks) else (
        "verify" if any(x["status"] == "unknown" for x in checks) or listing["stale"] or
        listing["collected_at"] is None else "matched" if checks else "explore")
    passed = [x for x in checks if x["status"] == "pass"]
    missing = [x for x in checks if x["status"] == "unknown"]
    failed = [x for x in checks if x["status"] == "fail"]
    if passed:
        reason = {"max_price_manwon": "설정한 호가 상한 안입니다.",
                  "min_area_m2": "원하는 전용면적 이상입니다.",
                  "max_monthly_manwon": "입력 가정의 월 부담 상한 안입니다.",
                  "region": "선택한 지역입니다."}[passed[0]["field"]]
    else:
        reason = "조건을 입력하면 적합성을 비교합니다." if not checks else "조건 충족을 확인하지 못했습니다."
    if preference["matched"]:
        reason = "선호 지역과 " + reason if passed else "선호 지역입니다."
    elif not passed and preference["satisfied"]:
        reason = "입력한 선호 조건 일부에 부합합니다."
    tradeoff = ("현재 알려진 조건에서는 양보할 점을 확인하지 못했습니다." if not failed else
                "입력 자본으로 구매비용을 충당할 수 없거나 월 부담 상한을 넘습니다." if failed[0]["field"] == "max_monthly_manwon" else
                f"{failed[0]['field']} 조건을 넘습니다.")
    verify = ("매물 판매 여부와 실제 호가를 확인하세요." if listing["stale"] else
              finance.get("reason") if missing and missing[0]["field"] == "max_monthly_manwon" and finance else
              f"{missing[0]['field']} 자료를 확인하세요." if missing else
              "실제 자금·매물 상태를 확인하세요.")
    return {"listing": listing, "eligibility": tier, "constraints": checks,
            "recommendation_reason": reason, "tradeoff": tradeoff,
            "verify_next": verify, "signal_context": row.get("시그널"),
            "finance": finance if "max_monthly_manwon" in spec else None,
            "preference": preference,
            "rank_version": VERSION}


def discover(rows: list[dict], spec: dict, *, source_fingerprint=None, finance_of=None) -> dict:
    spec = validate(spec)
    cursor = spec.pop("cursor", None)
    query_hash = _hash(spec)
    seen = set()
    groups = {"matched": [], "verify": [], "exceeded": [], "explore": []}
    for row in rows:
        if row.get("유형") not in KINDS or not row.get("key") or row["key"] in seen:
            continue
        seen.add(row["key"])
        finance = finance_of(row) if finance_of and "max_monthly_manwon" in spec else None
        item = classify(row, spec, finance=finance)
        groups[item["eligibility"]].append(item)
    def order(item):
        snap = item["listing"]
        return (-(item["preference"]["score"] or 0),
                -(item["preference"]["coverage"] or 0),
                -_freshness(snap),
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
        if not spec.get("region"):
            # In broad discovery, one district cannot monopolize the first page.
            selected, overflow, region_counts = [], [], {}
            for item in items:
                region = item["listing"]["region"]
                if region_counts.get(region, 0) < 4:
                    selected.append(item)
                    region_counts[region] = region_counts.get(region, 0) + 1
                else:
                    overflow.append(item)
            items[:] = selected + overflow
    counts = {k: len(v) for k, v in groups.items()}
    snapshot_hash = _hash({"groups": groups, "sources": source_fingerprint})
    if not spec["include_exceeded"]:
        groups["exceeded"] = []
    limit = spec["limit"]
    offset = 0
    if cursor:
        decoded = _decode_cursor(cursor)
        offset = decoded["offset"]
        if (decoded["version"] != VERSION or decoded["query"] != query_hash
                or type(offset) is not int or offset < 0 or offset % limit):
            raise ValueError("invalid_cursor")
        if decoded["snapshot"] != snapshot_hash:
            raise ValueError("stale_cursor")
        if offset >= max((len(items) for items in groups.values()), default=0):
            raise ValueError("invalid_cursor")
    next_offset = offset + limit
    more = any(len(items) > next_offset for items in groups.values())
    next_cursor = _encode_cursor({"version": VERSION, "query": query_hash,
                                  "snapshot": snapshot_hash, "offset": next_offset}) if more else None
    return {"version": VERSION, "groups": {k: v[offset:next_offset] for k, v in groups.items()},
            "counts": counts,
            "page": offset // limit + 1, "next_cursor": next_cursor,
            "basis": "수집된 일반·급매·찐매물의 호가와 확인 가능한 조건만 비교합니다. 구매 가능 확정이 아닙니다.",
            "generated_at": datetime.now(timezone.utc).isoformat()}
