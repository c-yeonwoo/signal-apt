"""Cached same-area evidence for listing cards; no source I/O or LLM calls."""

from realty_signal import db
from realty_signal.services import quote_check, property_analysis
from realty_signal.services import listing_price_history
from realty_signal.services.listing_inventory import SALE_KINDS


def _cache_key(row):
    code, name = row.get("지역코드"), row.get("단지명")
    if (not isinstance(code, str) or len(code) != 5 or not code.isdigit()
            or not isinstance(name, str) or not name.strip()
            or row.get("지역식별상태") != "matched" or row.get("source_conflict")):
        return None
    return f"complex:{code}:{name}"


def attach(rows: list[dict]) -> list[dict]:
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
    return listing_price_history.attach(out)
