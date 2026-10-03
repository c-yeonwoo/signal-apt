"""Read-only, aggregate inventory before any user-region key migration.

Current KB identity can establish a *candidate* for an old unique name, but
cannot prove what a user selected historically. This module never rewrites a
favorite, profile, alert or immutable decision/report record.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3

from realty_signal.db import AMBIGUOUS_LEGACY_REGION_KEYS
from realty_signal.services.signal_assessment import INCHEON_RETIRED_CODES


def classify(ref: object, kb, *, district_required: bool = False) -> str:
    if not isinstance(ref, str) or not ref.strip():
        return "missing"
    ref = ref.strip()
    if ref in AMBIGUOUS_LEGACY_REGION_KEYS:
        return "needs_reselection"
    if ref.startswith("kb:"):
        code = ref[3:]
        if len(code) != 10 or not code.isdigit():
            return "unverified"
        if code[:5] in INCHEON_RETIRED_CODES:
            return "needs_reselection"
        expected_name = None
    else:
        code = str((kb.codes or {}).get(ref) or "")
        if code[:5] in INCHEON_RETIRED_CODES:
            return "needs_reselection"
        expected_name = ref
    if not kb.identity_verified or len(code) != 10 or not code.isdigit():
        return "unverified"
    present = set(kb.regions)
    matches = [name for name, value in (kb.codes or {}).items()
               if str(value) == code and name in present]
    if len(matches) != 1 or (expected_name and matches[0] != expected_name):
        return "unverified"
    if district_required and code[2:5] == "000":
        return "not_district"
    return "verified_code" if ref.startswith("kb:") else "unique_legacy_name"


def _profile_ref(raw: str, kb) -> str:
    try:
        profile = json.loads(raw or "{}")
        if not isinstance(profile, dict):
            return "malformed"
        assumption = (profile.get("매수력") or {}).get("가정") or {}
        if not isinstance(assumption, dict):
            return "malformed"
        codes = [x for x in (profile.get("매수지역코드"), assumption.get("지역코드")) if x]
        if len(set(codes)) > 1:
            return "conflict"
        names = [x for x in (profile.get("매수지역"), assumption.get("지역")) if x]
        if len(set(names)) > 1:
            return "conflict"
        if not codes and not names:
            return "not_applicable"
        ref = codes[0] if codes else names[0]
        result = classify(ref, kb)
        if codes and names and result == "verified_code":
            matched = [name for name, value in (kb.codes or {}).items()
                       if str(value) == ref[3:] and name in set(kb.regions)]
            if matched != [names[0]]:
                return "conflict"
        return result
    except (TypeError, ValueError, AttributeError):
        return "malformed"


_SURFACES = {
    "region_favorites": ("favorites", "SELECT key FROM favorites WHERE kind='region'"),
    "complex_favorites": ("favorites", "SELECT key FROM favorites WHERE kind='complex'"),
    "listing_watches": ("listing_watch", "SELECT region FROM listing_watch"),
    "region_watch_state": ("region_watch_state_v2", "SELECT favorite_key FROM region_watch_state_v2"),
    "region_alerts": ("alert_outbox_v2", "SELECT subject_key FROM alert_outbox_v2 WHERE subject_type='region'"),
    "region_decision_notes": ("decision_notes_v2", "SELECT subject_key FROM decision_notes_v2 WHERE subject_type='region'"),
    "buyer_profiles": ("profile", "SELECT data FROM profile"),
}


def audit(db_path: Path, kb) -> dict:
    """Read existing SQLite only; output counts, never account IDs or location strings."""
    if not db_path.is_file():
        raise FileNotFoundError("app_db_missing")
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        surfaces = {}
        for surface, (table, query) in _SURFACES.items():
            if table not in tables:
                surfaces[surface] = {"table_absent": True}
                continue
            counts = Counter()
            for (value,) in connection.execute(query):
                if surface == "buyer_profiles":
                    counts[_profile_ref(value, kb)] += 1
                else:
                    ref = value.partition("|")[0] if surface == "complex_favorites" and isinstance(value, str) else value
                    counts[classify(ref, kb, district_required=surface == "complex_favorites")] += 1
            surfaces[surface] = dict(sorted(counts.items()))
        return {"kb_identity_verified": bool(kb.identity_verified),
                "kb_asof": str(kb.last_date.date()), "surfaces": surfaces,
                "note": "unique_legacy_name is an audit candidate, never permission to auto-migrate historic user intent"}
    finally:
        connection.close()
