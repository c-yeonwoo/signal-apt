"""Cached same-area evidence for listing cards; no source I/O or LLM calls."""

from bisect import bisect_left
from math import isfinite

from realty_signal import db
from realty_signal.services import quote_check, property_analysis
from realty_signal.services import listing_price_history
from realty_signal.services.listing_inventory import SALE_KINDS


_SOURCE_COMPLEX_FIELDS = {"baroezip": "complex_no", "hanbang": "hanbang_complex_id"}


def _asking_group(row):
    """Only compare verified, exact-area, same-provider current asking samples."""
    source = row.get("source")
    ref = row.get("ref") or {}
    complex_id = ref.get(_SOURCE_COMPLEX_FIELDS.get(source, ""))
    code = row.get("지역코드")
    area = ref.get("전용면적") or row.get("전용면적")
    try:
        area = round(float(area), 1)
    except (TypeError, ValueError):
        return None
    if (row.get("유형") not in SALE_KINDS or source not in _SOURCE_COMPLEX_FIELDS
            or type(complex_id) not in (str, int) or not complex_id or row.get("지역식별상태") != "matched"
            or row.get("source_conflict") or row.get("stale") or row.get("degraded")
            or not isinstance(code, str) or len(code) != 5 or not code.isdigit()
            or not isfinite(area) or area <= 0 or row.get("price_kind") != "asking"):
        return None
    return (source, code, str(complex_id), area)


def _asking_identity(row):
    ref = row.get("ref") or {}
    field = {"baroezip": "naver_id", "hanbang": "hanbang_id"}.get(row.get("source"))
    value = ref.get(field) if field else None
    return (row.get("source"), str(value)) if type(value) in (str, int) and value else None


def _asking_price(row):
    value = row.get("총액") or (row.get("ref") or {}).get("호가")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) and value > 0 else None


def _attach_asking_comparison(rows: list[dict], cohort_rows: list[dict]) -> list[dict]:
    cohorts = {}
    for row in cohort_rows:
        group = _asking_group(row)
        identity = _asking_identity(row)
        price = _asking_price(row)
        if group and identity and price is not None:
            # collapse() has already selected one current record for each proven source ID.
            cohorts.setdefault(group, {})[identity] = price
    ordered_cohorts = {group: sorted(prices.values()) for group, prices in cohorts.items()}
    result = []
    for row in rows:
        group, identity, price = _asking_group(row), _asking_identity(row), _asking_price(row)
        evidence = {"상태": "보류", "표본수": 0, "중앙값": None, "호가차이율": None,
                    "price_kind": "source_asking_sample", "scope": "same_provider_complex_area",
                    "공급사": row.get("source")}
        if group and identity and price is not None:
            ordered = ordered_cohorts.get(group, [])
            excluded = cohorts.get(group, {}).get(identity)
            count = len(ordered) - (excluded is not None)
            if count >= 3:
                # One current source identity is excluded, including when another listing
                # has the same asking price. The sorted cohort is built only once.
                cut = bisect_left(ordered, excluded) if excluded is not None else len(ordered)
                def peer_at(index):
                    return ordered[index + (index >= cut)] if excluded is not None else ordered[index]
                mid = count // 2
                median = peer_at(mid) if count % 2 else (peer_at(mid - 1) + peer_at(mid)) / 2
                evidence.update({"상태": "관측비교", "표본수": count, "중앙값": median,
                                 "호가차이율": round((price - median) / median * 100, 1)})
        result.append({**row, "asking_comparison": evidence})
    return result


def _cache_key(row):
    code, name = row.get("지역코드"), row.get("단지명")
    if (not isinstance(code, str) or len(code) != 5 or not code.isdigit()
            or not isinstance(name, str) or not name.strip()
            or row.get("지역식별상태") != "matched" or row.get("source_conflict")):
        return None
    return f"complex:{code}:{name}"


def attach(rows: list[dict], *, cohort_rows: list[dict] | None = None) -> list[dict]:
    """Read each complex once in bounded batches, then use the report's exact assessor."""
    keys = {_cache_key(row) for row in rows if row.get("유형") in SALE_KINDS}
    keys.discard(None)
    try:
        details = db.kv_get_many(sorted(keys), max_age=quote_check.MAX_SOURCE_AGE_DAYS * 86400)
    except Exception:  # Cache failure must not hide collected listings.
        details = {}
    out = []
    for row in rows:
        if row.get("유형") not in SALE_KINDS:
            out.append(row)
            continue
        detail = details.get(_cache_key(row))
        if not isinstance(detail, dict) or detail.get("schema_version") != 4:
            detail = {"status": "unavailable"}
        comparison = quote_check.assess_listing(detail, property_analysis.snapshot(row))
        out.append({**row, "price_comparison": comparison})
    out = listing_price_history.attach(out)
    return _attach_asking_comparison(out, cohort_rows if cohort_rows is not None else rows)
