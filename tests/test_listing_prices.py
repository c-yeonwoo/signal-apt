from copy import deepcopy
from datetime import timedelta
import time

import pytest

from realty_signal import db
from realty_signal.services import listing_prices, property_analysis, quote_check
from realty_signal.time_kst import today_kst


def row(area=59, price=50000, **kw):
    return {"key": f"일반매물:{area}", "유형": "일반매물", "단지명": "비교단지", "지역": "노원구",
            "지역코드": "11350", "지역식별상태": "matched", "source": "hanbang", "총액": price,
            "ref": {"전용면적": area, "층": 8}, **kw}


def detail():
    def area(size, price):
        return {"전용㎡": size, "비교거래": {"상태": "관측", "건수": 3,
                "기준일": today_kst().isoformat(), "중앙값": price, "최저": price, "최고": price,
                "층별표본": [{"층": n, "가격": price, "거래월": "2026-09"} for n in [7, 8, 9]]}}
    return {"schema_version": 4, "identity_status": "single_observed",
            "평형별": [area(59, 50000), area(84, 80000)]}


def test_cached_listing_and_report_use_identical_area_floor_and_source_contract(monkeypatch):
    data = detail()
    db.kv_set("complex:11350:비교단지", data)
    rows = [row(), row(84, 72000)]
    before = deepcopy(rows)
    calls = []
    original = db.kv_get_many
    monkeypatch.setattr(db, "kv_get_many", lambda keys, **kw: calls.append(keys) or original(keys, **kw))
    result = listing_prices.attach(rows)
    assert calls == [["complex:11350:비교단지"]]
    assert rows == before
    assert result[0]["price_comparison"]["호가차이율"] == 0
    assert result[1]["price_comparison"]["호가차이율"] == -10
    for source, enriched in zip(rows, result):
        assert enriched["price_comparison"] == property_analysis.build(source, data)["price"]
        assert enriched["price_comparison"]["price_kind"] == "source_asking"
        assert enriched["price_comparison"]["comparison_version"] == quote_check.VERSION


@pytest.mark.parametrize("case", ["missing", "expired", "old_schema", "bad_json", "old_observation", "other_area", "sparse", "bad_shape"])
def test_bad_cache_never_uses_complex_average_or_hides_listing(case):
    data = detail()
    if case == "old_schema":
        data["schema_version"] = 3
    if case == "old_observation":
        data["평형별"][0]["비교거래"]["기준일"] = (today_kst() - timedelta(days=8)).isoformat()
    if case == "other_area":
        data["평형별"] = data["평형별"][1:]
    if case == "sparse":
        data["평형별"][0]["비교거래"]["층별표본"] = []
    if case == "bad_shape":
        data["평형별"][0]["전용㎡"] = "알 수 없음"
    if case != "missing":
        db.kv_set("complex:11350:비교단지", data)
    if case in {"expired", "bad_json"}:
        c = db.conn()
        try:
            if case == "expired":
                c.execute("UPDATE kv SET ts=?", (time.time() - 8 * 86400,))
            else:
                c.execute("UPDATE kv SET v='not json'")
            c.commit()
        finally:
            c.close()
    result = listing_prices.attach([row()])
    assert len(result) == 1 and result[0]["총액"] == 50000
    assert result[0]["price_comparison"]["상태"] == "보류"
    assert result[0]["price_comparison"]["호가차이율"] is None


def test_unverified_region_is_not_used_as_another_district_cache_key(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "kv_get_many", lambda keys, **kw: calls.extend(keys) or {})
    listing_prices.attach([row(지역코드=None), row(지역식별상태="held"), row(source_conflict=True)])
    assert calls == []


def test_stale_listing_and_conflicting_identity_are_held_in_report_too():
    for source in [row(stale=True), row(source_conflict=True), row(지역식별상태="held")]:
        assert property_analysis.build(source, detail())["price"]["상태"] == "보류"


def test_auction_minimum_is_not_relabelled_as_a_collected_sale_asking():
    result = property_analysis.build(row(유형="경매"), detail())["price"]
    assert result["상태"] == "보류" and result["price_kind"] == "non_sale_price"


def test_list_report_and_discovery_endpoints_share_cached_evidence(monkeypatch):
    from fastapi.testclient import TestClient
    from realty_signal import api, auth
    from realty_signal.ingest import complex as cx

    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "price-owner@example.com")
    token, err = auth.signup("price-owner@example.com", "secret1", accept_tos=True)
    assert err is None
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, token)
    rows = [row(기회도=0), row(84, 72000, 기회도=0)]
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **kw: rows if kw.get("include_private") and "일반매물" in kinds else [])
    monkeypatch.setattr(api, "_attach_card_lines", lambda items, uid: items)
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api, "_display_signal_map", lambda: {})
    monkeypatch.setattr(api, "_code_of", lambda region: "11350")
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "regions": ["노원구"]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    monkeypatch.setattr(cx, "fetch_complex", lambda *a, **kw: pytest.fail("cache reads must not open trade source"))
    db.kv_set("complex:11350:비교단지", detail())
    listed = client.get("/api/listings/all", params={"types": "일반매물"})
    assert listed.status_code == 200
    prices = {r["key"]: r["price_comparison"] for r in listed.json()["listings"]}
    assert [prices[r["key"]]["호가차이율"] for r in rows] == [0, -10]
    for r in rows:
        report = client.get("/api/v2/listings/report", params={"key": r["key"]})
        assert report.status_code == 200
        assert report.json()["price"] == prices[r["key"]]
        assert report.headers["cache-control"] == "private, no-store"
    discovered = client.post("/api/v2/discovery", json={})
    assert discovered.status_code == 200
    items = [item for group in discovered.json()["groups"].values() for item in group]
    assert len(items) == 2
    assert all(item["listing"]["price_comparison"] == prices[item["listing"]["key"]] for item in items)


def test_bulk_cache_reads_are_chunked_read_only_and_tolerate_one_broken_entry(monkeypatch):
    for i in range(405):
        db.kv_set(f"test:{i}", {"index": i})
    before = db.DB.read_bytes()
    statements = []
    original = db.conn
    def connected():
        c = original()
        c.set_trace_callback(statements.append)
        return c
    monkeypatch.setattr(db, "conn", connected)
    rows = db.kv_get_many([f"test:{i}" for i in range(405)] * 2, max_age=60)
    assert len(rows) == 405
    assert len([s for s in statements if s.startswith("SELECT k,v,ts")]) == 2
    assert db.DB.read_bytes() == before


def test_cache_failure_keeps_all_source_rows(monkeypatch):
    monkeypatch.setattr(db, "kv_get_many", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("cache down")))
    rows = listing_prices.attach([row(), row(84, 80000)])
    assert len(rows) == 2 and all(r["price_comparison"]["상태"] == "보류" for r in rows)
