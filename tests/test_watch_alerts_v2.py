"""Watch changes are owner-scoped, cache-only and idempotent."""

import pytest
from fastapi import HTTPException

from realty_signal import db
from realty_signal.services import watch_alerts_v2 as alerts


def _watch():
    return {"key": "일반매물:a", "kind": "일반매물", "name": "기준단지",
            "region": "노원구", "saved_price": 50_000, "created_at": 100}


def _row(key="일반매물:a", price=50_000, *, stale=False, name="기준단지", complex_id="cx"):
    return {"key": key, "유형": "일반매물", "단지명": name, "지역": "노원구",
            "시도": "서울", "지역코드": "11350", "총액": price, "평형": 25,
            "stale": stale, "price_kind": "asking", "fetched_at": "2026-10-03", "ref": {"hanbang_id": key,
                                                    "hanbang_complex_id": complex_id}}


def test_first_observation_is_baseline_then_price_and_new_alternative_are_once_only():
    saved = [_watch()]
    c = db.conn()
    try:
        c.execute("INSERT INTO listing_watch VALUES(?,?,?,?,?,?,?)",
                  (7, "일반매물:a", "일반매물", "기준단지", "노원구", 50_000, 100))
        c.commit()
    finally:
        c.close()
    baseline = [_row(), _row("일반매물:b", 49_000)]
    assert alerts.materialize(7, saved, baseline, private_allowed=True) == 0
    assert alerts.list_events(7, private_allowed=True)["unread"] == 0
    changed = [_row(price=47_000), _row("일반매물:b", 49_000),
               _row("일반매물:c", 48_000)]
    assert alerts.materialize(7, saved, changed, private_allowed=True) == 2
    assert alerts.materialize(7, saved, changed, private_allowed=True) == 0
    feed = alerts.list_events(7, private_allowed=True)
    assert feed["unread"] == 2
    assert {item["kind"] for item in feed["items"]} == {"listing_price", "new_alternative"}
    price = next(item for item in feed["items"] if item["kind"] == "listing_price")
    assert price["payload"]["old_price"] == 50_000
    assert price["payload"]["new_price"] == 47_000
    new = next(item for item in feed["items"] if item["kind"] == "new_alternative")
    assert [item["key"] for item in new["payload"]["alternatives"]] == ["일반매물:c"]
    assert alerts.list_events(8, private_allowed=True) == {"items": [], "unread": 0}
    assert alerts.list_events(7, private_allowed=False) == {"items": [], "unread": 0}
    assert alerts.mark_seen(7, private_allowed=False) == 0
    assert alerts.mark_seen(7, private_allowed=True) == 2
    assert alerts.list_events(7, private_allowed=True)["unread"] == 0
    assert alerts.materialize(7, saved, [_row(price=50_000)], private_allowed=True) == 1
    assert alerts.list_events(7, private_allowed=True)["unread"] == 1


def test_missing_or_stale_source_never_becomes_a_sale_or_change():
    saved = [_watch()]
    assert alerts.materialize(7, saved, [_row()], private_allowed=True) == 0
    assert alerts.materialize(7, saved, [], private_allowed=True) == 0
    assert alerts.materialize(7, saved, [_row(price=44_000, stale=True)],
                              private_allowed=True) == 0
    assert alerts.list_events(7, private_allowed=True)["items"] == []
    assert alerts.materialize(7, saved, [_row(price=44_000)], private_allowed=True) == 1


def test_muted_rules_advance_baseline_without_retroactive_alert():
    saved = [_watch()]
    alerts.materialize(7, saved, [_row()], private_allowed=True)
    assert alerts.materialize(7, saved, [_row(price=46_000)], private_allowed=True,
                              prefs={"listing_price": False, "new_alternative": False}) == 0
    assert alerts.materialize(7, saved, [_row(price=46_000)], private_allowed=True) == 0
    assert alerts.list_events(7, private_allowed=True)["unread"] == 0


def test_unwatch_deletes_only_own_state_and_events():
    c = db.conn()
    try:
        c.executemany("INSERT INTO listing_watch VALUES(?,?,?,?,?,?,?)",
                      [(uid, "일반매물:a", "일반매물", "기준단지", "노원구", 50_000, 100)
                       for uid in (7, 8)])
        c.commit()
    finally:
        c.close()
    for uid in (7, 8):
        alerts.materialize(uid, [_watch()], [_row()], private_allowed=True)
        alerts.materialize(uid, [_watch()], [_row(price=45_000)], private_allowed=True)
    db.listing_watch_remove(7, "일반매물:a")
    assert alerts.list_events(7, private_allowed=True)["items"] == []
    assert alerts.list_events(8, private_allowed=True)["unread"] == 1
    db.listing_watch_add(7, {"key": "일반매물:a", "유형": "일반매물",
                             "단지명": "기준단지", "지역": "노원구", "총액": 45_000})
    assert alerts.materialize(7, [_watch()], [_row(price=45_000)], private_allowed=True) == 0
    assert alerts.list_events(7, private_allowed=True)["items"] == []


def test_scheduled_scan_reads_cache_once_and_respects_private_owner(monkeypatch):
    from realty_signal import api, config

    owner = db.user_create("owner@example.com", "test")
    other = db.user_create("other@example.com", "test")
    for uid in (owner, other):
        db.listing_watch_add(uid, {"key": "일반매물:a", "유형": "일반매물",
                                   "단지명": "기준단지", "지역": "노원구", "총액": 50_000})
    monkeypatch.setattr(config, "personal_listing_allowed", lambda email: email == "owner@example.com")
    current = {"rows": [_row()]}
    calls = []
    monkeypatch.setattr(api, "_build_listings", lambda kinds, *, include_private: (
        calls.append((kinds, include_private)) or current["rows"]))
    assert alerts.scan_cached_sources() == 0
    current["rows"] = [_row(price=45_000)]
    assert alerts.scan_cached_sources() == 1
    assert calls == [({"일반매물"}, True), ({"일반매물"}, True)]
    assert alerts.list_events(owner, private_allowed=True)["unread"] == 1
    assert alerts.list_events(other, private_allowed=True)["unread"] == 0


def test_public_watch_cannot_leak_private_alternative_from_shared_cache():
    saved = [{"key": "경매:a", "kind": "경매", "name": "기준단지",
              "region": "노원구", "saved_price": 50_000}]
    c = db.conn()
    try:
        c.execute("INSERT INTO listing_watch VALUES(?,?,?,?,?,?,?)",
                  (7, "경매:a", "경매", "기준단지", "노원구", 50_000, 100))
        c.commit()
    finally:
        c.close()
    public = {**_row("경매:a"), "유형": "경매"}
    private = _row("일반매물:secret", name="비공개 대안")
    assert alerts.materialize(7, saved, [public, private], private_allowed=False) == 0
    public_alternative = {**_row("경매:b"), "유형": "경매"}
    assert alerts.materialize(7, saved, [public, private, public_alternative],
                              private_allowed=False) == 1
    payload = alerts.list_events(7, private_allowed=False)["items"][0]["payload"]
    assert [item["key"] for item in payload["alternatives"]] == ["경매:b"]
    assert "비공개" not in str(payload)


def test_auction_minimum_bid_is_not_reported_as_asking_price_change():
    saved = [{"key": "경매:a", "kind": "경매", "name": "기준단지",
              "region": "노원구", "saved_price": 50_000}]
    first = {**_row("경매:a"), "유형": "경매", "price_kind": None}
    later = {**first, "총액": 40_000}
    assert alerts.materialize(7, saved, [first], private_allowed=True) == 0
    assert alerts.materialize(7, saved, [later], private_allowed=True) == 0


def test_watch_mutations_require_login(monkeypatch):
    from realty_signal.routes import market

    monkeypatch.setattr(market.deps, "uid", lambda request: None)
    with pytest.raises(HTTPException) as adding:
        market.listing_watch_add(None, {"key": "일반매물:a"})
    with pytest.raises(HTTPException) as removing:
        market.listing_watch_remove(None, "일반매물:a")
    assert adding.value.status_code == removing.value.status_code == 401


def test_alert_api_includes_and_marks_in_app_watch_events(monkeypatch):
    from realty_signal import api
    from realty_signal.routes import alerts as routes

    db.listing_watch_add(7, {"key": "일반매물:a", "유형": "일반매물",
                             "단지명": "기준단지", "지역": "노원구", "총액": 50_000})
    alerts.materialize(7, [_watch()], [_row()], private_allowed=True)
    alerts.materialize(7, [_watch()], [_row(price=45_000)], private_allowed=True)
    monkeypatch.setattr(routes.deps, "uid", lambda request: 7)
    monkeypatch.setattr(routes.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(db, "actionable_region_favs", lambda uid: [])
    monkeypatch.setattr(api, "_display_signal_map", lambda: {})
    result = routes.alerts(None)
    assert result["unread"] == 1
    assert result["watch_events"][0]["kind"] == "listing_price"
    assert routes.alerts_seen(None) == {"ok": True}
    assert routes.alerts(None)["unread"] == 0
