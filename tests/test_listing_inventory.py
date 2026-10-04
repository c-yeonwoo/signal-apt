from copy import deepcopy
import json

import pytest

from realty_signal.services.listing_inventory import collapse
from realty_signal.services.property_analysis import snapshot, resolve
from realty_signal import api
from realty_signal.services.discovery_v2 import classify


def row(kind="급매", **kw):
    return {"key": f"{kind}:123", "유형": kind, "source": "baroezip", "단지명": "비교단지",
            "지역": "노원구", "지역코드": "11350", "시도": "서울", "총액": 50000,
            "ref": {"naver_id": "123", "complex_no": "1", "전용면적": 59, "층": 8},
            "supplier_flags": ["urgent"] if kind == "급매" else ["certified"],
            "fetched_at": 100, **kw}


def test_duplicate_source_id_uses_freshest_not_cheapest_and_preserves_keys():
    rows = [row(), row("찐매물", fetched_at=200, 총액=51000)]
    before = deepcopy(rows)
    merged = collapse(rows)
    assert rows == before
    assert len(merged) == 1
    assert merged[0]["key"] == "찐매물:123"
    assert merged[0]["총액"] == 51000
    assert set(merged[0]["listing_aliases"]) == {"급매:123", "찐매물:123"}
    assert merged[0]["supplier_flags"] == ["certified", "urgent"]
    assert snapshot(merged[0])["listing_aliases"] == merged[0]["listing_aliases"]
    assert collapse(rows[::-1]) == merged


def test_stale_flags_and_lower_price_do_not_override_fresh_record():
    merged = collapse([row(stale=True, fetched_at=300, 총액=30000), row("찐매물", fetched_at=200)])
    assert merged[0]["key"] == "찐매물:123"
    assert merged[0]["supplier_flags"] == ["certified"]


@pytest.mark.parametrize("field,value", [("전용면적", 84), ("층", 1), ("complex_no", "2")])
def test_conflicting_source_identity_is_not_silently_merged(field, value):
    other = row("찐매물")
    other["ref"][field] = value
    result = collapse([row(), other])
    assert len(result) == 2 and all(r["source_conflict"] for r in result)
    assert all(r["listing_aliases"] == [r["key"]] for r in result)
    decision = classify(result[0], {"max_price_manwon": 60000})
    assert decision["eligibility"] == "verify"
    assert decision["constraints"][0]["status"] == "unknown"
    assert "서로 달라" in decision["recommendation_reason"]


def test_equal_name_price_floor_or_different_source_ids_do_not_prove_identity():
    a = row()
    b = row("일반매물", source="hanbang", ref={"hanbang_id": "123", "전용면적": 59, "층": 8})
    c = row("찐매물", ref={"전용면적": 59, "층": 8})
    d = row("찐매물", key="찐매물:456", ref={"naver_id": "456", "전용면적": 59, "층": 8})
    assert len(collapse([a, b, c, d])) == 4


def test_old_report_keys_resolve_independently_and_private_guard_stays(monkeypatch):
    rows = [row(), row("찐매물")]
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [r for r in rows if r["유형"] in kinds])
    assert resolve("급매:123", private_allowed=True)["key"] == "급매:123"
    assert resolve("찐매물:123", private_allowed=True)["key"] == "찐매물:123"
    with pytest.raises(PermissionError):
        resolve("찐매물:123", private_allowed=False)


def test_discovery_deduplicates_before_counts_and_pagination(monkeypatch):
    from realty_signal.routes import reports_v2
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "empty", "listings": []})
    for name in ("quicksale", "certified"):
        monkeypatch.setattr(api, name, lambda: {"state": "ready", "listings": [{}]})
    rows = [row(), row("찐매물", fetched_at=200)]
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [r for r in rows if r["유형"] in kinds])
    response = reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1})
    result = json.loads(response.body)
    assert result["counts"]["matched"] == 1
    assert result["groups"]["matched"][0]["listing"]["key"] == "찐매물:123"
    assert len(result["groups"]["matched"][0]["listing"]["listing_aliases"]) == 2
