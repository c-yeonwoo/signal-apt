"""Deterministic listing discovery with explicit pass, unknown and fail tiers."""

from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite

from realty_signal.services.property_analysis import snapshot

VERSION = "discovery-v2-15"
KINDS = {"일반매물", "급매", "찐매물"}
FIELDS = {"max_price_manwon", "min_area_m2", "min_rooms", "move_in_by", "max_commute_minutes", "max_monthly_manwon", "region", "prefer_region",
          "region_code", "prefer_region_code", "prefer_max_price_manwon", "prefer_min_area_m2", "priority"}
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
    if spec.get("min_rooms") is not None:
        value = spec["min_rooms"]
        if type(value) is not int or not 1 <= value <= 15:
            raise ValueError("invalid_condition_value")
        cleaned["min_rooms"] = value
    if spec.get("max_commute_minutes") is not None:
        value = spec["max_commute_minutes"]
        if type(value) is not int or not 1 <= value <= 240:
            raise ValueError("invalid_condition_value")
        cleaned["max_commute_minutes"] = value
    if spec.get("move_in_by") is not None:
        value = spec["move_in_by"]
        if not isinstance(value, str):
            raise ValueError("invalid_move_in_date")
        try:
            day = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid_move_in_date") from exc
        if value != day.isoformat() or not 2020 <= day.year <= 2100:
            raise ValueError("invalid_move_in_date")
        cleaned["move_in_by"] = value
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
    for key in ("region_code", "prefer_region_code"):
        if spec.get(key) is not None:
            code = spec[key]
            if not isinstance(code, str) or len(code) != 5 or not code.isdigit():
                raise ValueError("invalid_region_code")
            cleaned[key] = code
    if sum(bool(cleaned.get(key)) for key in ("region", "prefer_region", "region_code", "prefer_region_code")) > 1:
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


def _move_date(occupancy: dict | None) -> str | None:
    if not isinstance(occupancy, dict) or occupancy.get("status") not in {"dated", "immediate"}:
        return None
    value = occupancy.get("date")
    if not isinstance(value, str):
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return value if value == day.isoformat() else None


def _region_code(listing: dict) -> str | None:
    code = str(listing.get("region_code") or "")
    return code if len(code) == 5 and code.isdigit() else None


def _preference(listing: dict, spec: dict) -> dict:
    """One explicit priority doubles its weight; unknowns remain in the denominator."""
    details = []
    region_code = spec.get("prefer_region_code")
    region_value = _region_code(listing) if region_code else listing["region"]
    definitions = (("region", "선호 지역", region_code or spec.get("prefer_region"),
                    region_value, bool(region_value), lambda v, target: v == target),
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
    return {"region": spec.get("prefer_region_code") or spec.get("prefer_region"),
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


def classify(row: dict, spec: dict, *, finance: dict | None = None,
             region_hint: dict | None = None, occupancy: dict | None = None,
             commute: dict | None = None) -> dict:
    listing = snapshot(row)
    if "move_in_by" in spec:
        listing["move_in"] = occupancy
    if "max_commute_minutes" in spec:
        listing["commute"] = ({key: commute.get(key) for key in (
            "status", "minutes", "checked_at", "origin_quality", "source", "source_url")}
            if isinstance(commute, dict) else None)
    price = listing["asking_manwon"]
    area = listing["exclusive_m2"]
    rooms = listing["rooms"]
    checks = []
    if "max_price_manwon" in spec:
        checks.append({"field": "max_price_manwon", "status": "unknown" if price is None or listing["stale"]
                       else "pass" if price <= spec["max_price_manwon"] else "fail",
                       "value": price, "limit": spec["max_price_manwon"]})
    if "min_area_m2" in spec:
        checks.append({"field": "min_area_m2", "status": "unknown" if area is None
                       else "pass" if area >= spec["min_area_m2"] else "fail",
                       "value": area, "limit": spec["min_area_m2"]})
    if "min_rooms" in spec:
        checks.append({"field": "min_rooms", "status": "unknown" if rooms is None
                       else "pass" if rooms >= spec["min_rooms"] else "fail",
                       "value": rooms, "limit": spec["min_rooms"]})
    if "move_in_by" in spec:
        move_date = _move_date(occupancy)
        checks.append({"field": "move_in_by", "status": "unknown" if move_date is None
                       else "pass" if move_date <= spec["move_in_by"] else "fail",
                       "value": move_date, "limit": spec["move_in_by"]})
    if "max_commute_minutes" in spec:
        minutes = commute.get("minutes") if isinstance(commute, dict) and commute.get("status") == "observed" else None
        checks.append({"field": "max_commute_minutes", "status": "unknown" if type(minutes) is not int
                       else "pass" if minutes <= spec["max_commute_minutes"] else "fail",
                       "value": minutes, "limit": spec["max_commute_minutes"]})
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
    if "region_code" in spec:
        region_code = _region_code(listing)
        status = "pass" if region_code == spec["region_code"] else "fail" if region_code else "unknown"
        if status == "unknown" and region_hint:
            # A missing code cannot establish a match, but a source-verified
            # different district/province can safely rule one out.
            actual = "".join((listing.get("region") or "").split())
            expected = "".join((region_hint.get("name") or "").split())
            actual_sido = listing.get("region_sido")
            expected_sido = region_hint.get("sido")
            if (actual and expected and actual != expected) or (
                    actual_sido and expected_sido and actual_sido != expected_sido):
                status = "fail"
        checks.append({"field": "region_code", "status": status,
                       "value": region_code, "limit": spec["region_code"]})
    if listing.get("source_conflict"):
        checks = [{**check, "status": "unknown"} for check in checks]
    preference = _preference(listing, spec)
    tier = "exceeded" if any(x["status"] == "fail" for x in checks) else (
        "verify" if any(x["status"] == "unknown" for x in checks) or listing["stale"] or
        listing["collected_at"] is None or listing.get("source_conflict") else "matched" if checks else "explore")
    passed = [x for x in checks if x["status"] == "pass"]
    missing = [x for x in checks if x["status"] == "unknown"]
    failed = [x for x in checks if x["status"] == "fail"]
    if passed:
        reason = {"max_price_manwon": "설정한 호가 상한 안입니다.",
                  "min_area_m2": "원하는 전용면적 이상입니다.",
                  "min_rooms": "원하는 방 개수 이상입니다.",
                  "move_in_by": "표시된 입주 가능일이 원하는 시점 이내입니다.",
                  "max_commute_minutes": "매물 표시 좌표 기준 대중교통 안내시간이 설정한 상한 안입니다.",
                  "max_monthly_manwon": "입력 가정의 월 부담 상한 안입니다.",
                  "region": "선택한 지역입니다.", "region_code": "선택한 지역입니다."}[passed[0]["field"]]
    else:
        unknown_reason = {
            "max_price_manwon": ("호가가 없어 예산 부합을 판단하지 않았습니다." if price is None else
                                 "지난 수집 호가라 현재 예산 부합을 판단하지 않았습니다."),
            "min_area_m2": "전용면적이 없어 원하는 면적 충족 여부를 판단하지 않았습니다.",
            "min_rooms": "방 개수가 없어 원하는 조건 충족 여부를 판단하지 않았습니다.",
            "move_in_by": "입주 가능일이 확인되지 않아 필요한 시점인지 판단하지 않았습니다.",
            "max_commute_minutes": "대중교통 안내시간이 확인되지 않아 통근 조건을 판단하지 않았습니다.",
            "max_monthly_manwon": "자금·정책 가정이 확인되지 않아 월 부담 조건을 판단하지 않았습니다.",
            "region_code": "매물의 시군구 코드가 없어 선택한 지역인지 확인되지 않았습니다.",
        }
        if failed:
            reason = "확인된 필수 조건을 넘습니다."
        elif missing:
            reason = unknown_reason.get(missing[0]["field"], "확인되지 않은 조건이 있습니다.")
        else:
            reason = "조건을 입력하면 적합성을 비교합니다."
    if preference["matched"] and passed:
        reason = "선호 지역과 " + reason
    elif not checks and preference["matched"]:
        reason = "선호 지역입니다."
    elif not checks and preference["satisfied"]:
        reason = "입력한 선호 조건 일부에 부합합니다."
    failed_reason = {"max_price_manwon": "호가가 설정한 상한보다 높습니다.",
                     "min_area_m2": "전용면적이 원하는 최소 면적보다 작습니다.",
                     "min_rooms": "방 개수가 원하는 최소보다 적습니다.",
                     "move_in_by": "표시된 입주 가능일이 원하는 시점보다 늦습니다.",
                     "max_commute_minutes": "매물 표시 좌표 기준 대중교통 안내시간이 설정한 상한보다 깁니다.",
                     "max_monthly_manwon": "입력 자본으로 구매비용을 충당할 수 없거나 월 부담 상한을 넘습니다.",
                     "region": "선택한 필수 지역 밖의 매물입니다.",
                     "region_code": "선택한 필수 지역 밖의 매물입니다."}
    tradeoff = (" ".join(failed_reason[x["field"]] for x in failed) if failed else
                "현재 알려진 조건에서는 양보할 점을 확인하지 못했습니다.")
    verify = (finance.get("reason") if missing and missing[0]["field"] == "max_monthly_manwon" and finance else
              "매물의 현재 호가와 판매 여부를 확인하세요." if missing and missing[0]["field"] == "max_price_manwon" else
              "매물의 전용면적을 확인하세요." if missing and missing[0]["field"] == "min_area_m2" else
              "매물의 시군구 코드를 확인하세요." if missing and missing[0]["field"] == "region_code" else
              "매물의 방 개수를 확인하세요." if missing and missing[0]["field"] == "min_rooms" else
              "입주 가능일이 시기 표현이거나 미확인입니다. 중개사에게 실제 입주일을 확인하세요." if missing and missing[0]["field"] == "move_in_by" else
              "저장된 직장까지의 대중교통 경로를 확인하세요." if missing and missing[0]["field"] == "max_commute_minutes" else
              "확정 자금과 월 부담 가정을 확인하세요." if missing and missing[0]["field"] == "max_monthly_manwon" else
              "확인되지 않은 조건의 원천 자료를 확인하세요." if missing else
              "매물 판매 여부와 실제 호가를 확인하세요." if listing["stale"] else
              "실제 자금·매물 상태를 확인하세요.")
    stale_over_budget = (listing["stale"] and price is not None
                         and "max_price_manwon" in spec and price > spec["max_price_manwon"])
    if stale_over_budget and tier == "verify" and not listing.get("source_conflict"):
        reason = "지난 수집 호가는 설정한 상한보다 높았습니다. 현재 가격은 확인되지 않아 예산 적합성을 보류했습니다."
        tradeoff = "지난 호가 기준으로는 예산을 넘었습니다."
        verify = "판매 여부와 최신 호가가 예산 안으로 내려왔는지 원천에서 확인하세요."
    if listing.get("source_conflict"):
        reason = "같은 원천 ID의 매물 정보가 서로 달라 조건 판정을 보류했습니다."
        verify = "원천에서 단지·전용면적·층, 판매 여부와 실제 호가를 먼저 확인하세요."
    return {"listing": listing, "eligibility": tier, "constraints": checks,
            "recommendation_reason": reason, "tradeoff": tradeoff,
            "verify_next": verify, "signal_context": row.get("시그널"),
            "finance": finance if "max_monthly_manwon" in spec else None,
            "preference": preference,
            "rank_version": VERSION}


def discover(rows: list[dict], spec: dict, *, source_fingerprint=None, finance_of=None,
             region_hint: dict | None = None, occupancy_by_key: dict | None = None,
             commute_by_key: dict | None = None) -> dict:
    spec = validate(spec)
    cursor = spec.pop("cursor", None)
    query_hash = _hash({"conditions": spec, "region_hint": region_hint})
    seen = set()
    groups = {"matched": [], "verify": [], "exceeded": [], "explore": []}
    for row in rows:
        if row.get("유형") not in KINDS or not row.get("key") or row["key"] in seen:
            continue
        seen.add(row["key"])
        finance = finance_of(row) if finance_of and "max_monthly_manwon" in spec else None
        item = classify(row, spec, finance=finance, region_hint=region_hint,
                        occupancy=(occupancy_by_key or {}).get(row["key"]),
                        commute=(commute_by_key or {}).get(row["key"]))
        groups[item["eligibility"]].append(item)
    def unverified_quote_priority(item):
        snap = item["listing"]
        if item["eligibility"] != "verify":
            return 0
        if ("max_price_manwon" in spec and snap["asking_manwon"] is not None
                and snap["asking_manwon"] > spec["max_price_manwon"]
                and (snap["stale"] or snap.get("source_conflict"))):
            return 2
        return 1 if snap.get("source_conflict") else 0
    def order(item):
        snap = item["listing"]
        # 입주일 조건을 요청한 경우에만, 같은 선호 적합도·확인도 안에서
        # 사용자가 실제로 상세 확인할 수 있는 한방 후보를 먼저 보여 준다.
        move_lookup = (0 if "move_in_by" in spec and item["eligibility"] == "verify"
                       and snap["kind"] == "일반매물" and snap.get("move_in") is None else 1)
        commute_lookup = (0 if "max_commute_minutes" in spec and item["eligibility"] == "verify"
                          and snap.get("coordinate") and snap.get("commute") is None else 1)
        return (unverified_quote_priority(item),
                -(item["preference"]["score"] or 0),
                -(item["preference"]["coverage"] or 0),
                move_lookup,
                commute_lookup,
                -_freshness(snap),
                snap["key"])
    for items in groups.values():
        items.sort(key=order)
        # Keep the first page useful when one complex has many individual listings.
        selected, overflow, complex_counts = [], [], {}
        for item in items:
            listing = item["listing"]
            complex_key = (listing["region_code"] or listing["region_sido"], listing["region"], listing["name"])
            if complex_counts.get(complex_key, 0) < 2:
                selected.append(item)
                complex_counts[complex_key] = complex_counts.get(complex_key, 0) + 1
            else:
                overflow.append(item)
        items[:] = selected + overflow
        if not spec.get("region") and not spec.get("region_code"):
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
        if items is groups["verify"]:
            # Diversity passes may move a conflicting or previously over-budget
            # quote forward; keep uncertain price evidence after ordinary rows.
            items.sort(key=unverified_quote_priority)
    counts = {k: len(v) for k, v in groups.items()}
    # Count only candidates that become a basic candidate by relaxing exactly
    # one failed condition. Unknown or stale facts must not be promoted.
    relaxations = {}
    for item in groups["exceeded"]:
        checks = item["constraints"]
        failures = [check for check in checks if check["status"] == "fail"]
        if (len(failures) == 1 and all(check["status"] != "unknown" for check in checks)
                and not item["listing"]["stale"] and item["listing"]["collected_at"]):
            field = failures[0]["field"]
            relaxations[field] = relaxations.get(field, 0) + 1
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
            "single_condition_relaxations": relaxations,
            "page": offset // limit + 1, "next_cursor": next_cursor,
            "basis": "수집된 일반·급매·찐매물의 호가와 확인 가능한 조건만 비교합니다. 구매 가능 확정이 아닙니다.",
            "generated_at": datetime.now(timezone.utc).isoformat()}
