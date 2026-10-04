"""Unified read model; source keys and stored user records are never rewritten."""

from math import isfinite

SALE_KINDS = {"일반매물", "급매", "찐매물"}


def _identity(row):
    ref = row.get("ref") or {}
    source = row.get("source")
    field = {"baroezip": "naver_id", "hanbang": "hanbang_id"}.get(source)
    value = ref.get(field) if field else None
    return (source, str(value)) if type(value) in (str, int) and value and row.get("유형") in SALE_KINDS else None


def _timestamp(row):
    value = row.get("fetched_at")
    return value if type(value) in (int, float) and isfinite(value) else 0


def collapse(rows: list[dict]) -> list[dict]:
    """Collapse only proven same-source IDs, keeping the freshest usable observation.

    Conflicting identity/area/floor facts stay separate. A name or equal asking price
    never proves that two advertisements are the same home. Source counts may overlap.
    """
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(_identity(row) or ("unmatched", i), []).append(row)
    out = []
    for group in groups.values():
        signatures = [{str(r.get(field)) for r in group if r.get(field) not in (None, "")}
                      for field in ("지역코드", "시도", "단지명")]
        signatures += [{str((r.get("ref") or {}).get(field)) for r in group
                        if (r.get("ref") or {}).get(field) not in (None, "")}
                       for field in ("complex_no", "hanbang_complex_id", "전용면적", "층")]
        conflict = any(len(values) > 1 for values in signatures)
        if conflict:
            out.extend({**row, "source_conflict": True, "listing_aliases": [row.get("key")],
                        "source_labels": [row.get("유형")]} for row in group)
            continue
        # Prefer non-stale, non-degraded data, then actual collection time. Never lowest price.
        selected = min(group, key=lambda row: (bool(row.get("stale") or row.get("degraded")),
                       -_timestamp(row), str(row.get("key") or "")))
        usable = [r for r in group if not r.get("stale") and not r.get("degraded")] or [selected]
        out.append({**selected,
                    "listing_aliases": sorted({r["key"] for r in group if r.get("key")}),
                    "source_labels": sorted({r["유형"] for r in group if r.get("유형")}),
                    "supplier_flags": sorted({flag for r in usable for flag in r.get("supplier_flags", [])}),
                    "source_record_count": len(group)})
    return out
