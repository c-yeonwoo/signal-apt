"""매물 찜: ID 안정성, 가격·청약 일정, 대안, 개인용 원천 격리."""

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.services import listing_watch as watch


def _row(key, name="A", price=50_000, *, kind="급매", region="노원구", complex_no="1"):
    return {"key": key, "유형": kind, "단지명": name, "지역": region,
            "총액": price, "평형": 25, "price_kind": "asking" if kind in watch.PRIVATE else None,
            "ref": {"complex_no": complex_no}}


def _client(email):
    token, err = auth.signup(email, "secret1", accept_tos=True)
    assert err is None
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, token)
    return client


def test_price_change_same_complex_and_similar_are_not_confused():
    saved = [{"key": "급매:1", "kind": "급매", "name": "A", "region": "노원구",
              "saved_price": 60_000, "created_at": 1}]
    current = [_row("급매:1", price=50_000), _row("급매:2", price=52_000),
               _row("급매:3", name="B", price=53_000, complex_no="2"),
               _row("급매:4", name="C", price=90_000, complex_no="3")]
    current[0]["ref"]["naver_id"] = "same-source-id"
    duplicate = _row("찐매물:1", price=50_000, kind="찐매물")
    duplicate["ref"]["naver_id"] = "same-source-id"
    current.append(duplicate)
    result = watch.build(saved, current)[0]
    assert result["price_change"] == -10_000
    assert [(x["key"], x["reason"]) for x in result["alternatives"]] == [
        ("급매:2", "같은 단지의 다른 매물"),
        ("급매:3", "같은 지역·유형·비슷한 가격대")]


def test_same_named_district_in_other_province_is_not_watch_alternative():
    seoul = {**_row("급매:seoul", name="중구아파트", region="중구"), "시도": "서울"}
    incheon = {**_row("급매:incheon", name="중구아파트", region="중구"), "시도": "인천"}
    unknown = _row("급매:unknown", name="중구아파트", region="중구")
    saved = [{"key": seoul["key"], "saved_price": seoul["총액"]}]
    assert watch.build(saved, [seoul, incheon, unknown])[0]["alternatives"] == []


def test_unseen_listing_is_not_called_sold_and_presale_dday_is_kept():
    saved = [{"key": "급매:missing", "kind": "급매", "name": "A", "region": "노원구",
              "saved_price": 50_000, "created_at": 1}]
    result = watch.build(saved, [])
    assert result[0]["current"] is None and result[0]["price_change"] is None
    presale = _row("청약:123", kind="청약", price=None)
    presale["ref"]["Dday"] = 0
    assert watch.public_fields(presale)["dday"] == 0
    stale = _row("급매:missing", price=40_000)
    stale["stale"] = True
    assert watch.build(saved, [stale])[0]["price_change"] is None
    auction = _row("경매:123", kind="경매", price=50_000)
    assert watch.build([{"key": "경매:123", "kind": "경매", "saved_price": 60_000}],
                       [auction])[0]["price_change"] is None


def test_private_watch_only_owner_and_save_resolves_source(monkeypatch):
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "owner@example.com")
    rows = [_row("급매:1"), _row("급매:2", name="B", complex_no="2")]
    monkeypatch.setattr(api, "_build_listings", lambda kinds, include_private=False:
                        [x for x in rows if x["유형"] in kinds] if include_private else [])
    owner, guest = _client("owner@example.com"), _client("guest@example.com")
    assert guest.post("/api/listing-watch", json={"key": "급매:1"}).status_code == 403
    assert owner.post("/api/listing-watch", json={"key": "급매:unknown"}).status_code == 404
    assert owner.post("/api/listing-watch", json={"key": "급매:1"}).json()["ok"]
    assert owner.post("/api/listing-watch", json={"key": "급매:1"}).json()["ok"]
    assert owner.put("/api/listing-watch/price-target", json={
        "key": "급매:1", "target_manwon": 47_000}).json()["ok"]
    assert owner.get("/api/listing-watch").json()["items"][0]["target_price"] == 47_000
    assert guest.put("/api/listing-watch/price-target", json={
        "key": "급매:1", "target_manwon": 47_000}).status_code == 403
    assert len(owner.get("/api/listing-watch").json()["items"]) == 1
    assert guest.get("/api/listing-watch").json()["items"] == []
    assert owner.delete("/api/listing-watch", params={"key": "급매:1"}).json()["ok"]
    assert owner.get("/api/listing-watch").json()["items"] == []


def test_watch_rows_are_user_scoped():
    db.listing_watch_add(1, _row("청약:123", kind="청약", price=None))
    assert len(db.listing_watch_list(1)) == 1
    assert db.listing_watch_list(2) == []
    db.listing_watch_remove(2, "청약:123")
    assert len(db.listing_watch_list(1)) == 1


def test_listing_watch_owns_only_the_complex_interest_it_created():
    complex_key = "kb:1135000000|A"
    first, second = _row("일반매물:one", kind="일반매물"), _row("일반매물:two", kind="일반매물")
    db.listing_watch_add(7, first, complex_key=complex_key)
    db.listing_watch_add(7, second, complex_key=complex_key)
    assert [f["key"] for f in db.fav_list(7)] == [complex_key]
    db.listing_watch_remove(7, first["key"])
    assert [f["key"] for f in db.fav_list(7)] == [complex_key]

    db.listing_watch_remove(7, second["key"])
    assert db.fav_list(7) == []

    db.listing_watch_add(7, first, complex_key=complex_key)
    db.fav_add(7, "complex", complex_key, "A")
    db.listing_watch_remove(7, first["key"])
    assert [f["key"] for f in db.fav_list(7)] == [complex_key]

    db.fav_add(7, "complex", complex_key, "A")
    db.listing_watch_add(7, first, complex_key=complex_key)
    db.listing_watch_remove(7, first["key"])
    assert [f["key"] for f in db.fav_list(7)] == [complex_key]


def test_watching_verified_sale_adds_complex_interest_without_cross_account_access(monkeypatch):
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "owner@example.com")
    item = {**_row("일반매물:one", kind="일반매물"), "지역코드": "11350",
            "지역식별상태": "matched", "source": "hanbang"}
    monkeypatch.setattr(api, "_build_listings", lambda kinds, include_private=False:
                        [item] if include_private and "일반매물" in kinds else [])
    owner, guest = _client("owner@example.com"), _client("guest@example.com")
    assert owner.post("/api/listing-watch", json={"key": item["key"]}).status_code == 200
    assert [f["key"] for f in db.fav_list(auth.current_user(owner.cookies.get(auth.COOKIE))["id"])] == [
        "kb:1135000000|A"]
    assert guest.get("/api/listing-watch").json()["items"] == []
    assert owner.delete("/api/listing-watch", params={"key": item["key"]}).status_code == 200
    assert db.fav_list(auth.current_user(owner.cookies.get(auth.COOKIE))["id"]) == []


def test_watching_sale_reuses_unambiguous_legacy_complex_interest(monkeypatch):
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "owner@example.com")
    item = {**_row("일반매물:one", kind="일반매물"), "지역코드": "11350",
            "지역식별상태": "matched"}
    monkeypatch.setattr(api, "_build_listings", lambda kinds, include_private=False: [item])
    owner = _client("owner@example.com")
    uid = auth.current_user(owner.cookies.get(auth.COOKIE))["id"]
    db.fav_add(uid, "complex", "노원구|A", "A")
    assert owner.post("/api/listing-watch", json={"key": item["key"]}).status_code == 200
    assert [f["key"] for f in db.fav_list(uid)] == ["노원구|A"]
    assert owner.delete("/api/listing-watch", params={"key": item["key"]}).status_code == 200
    assert [f["key"] for f in db.fav_list(uid)] == ["노원구|A"]


def test_price_target_api_requires_owner_and_sale_asking_kind(monkeypatch):
    from realty_signal.routes import market

    db.listing_watch_add(7, _row("일반매물:a", kind="일반매물"))
    db.listing_watch_add(7, _row("경매:a", kind="경매"))
    monkeypatch.setattr(market.deps, "uid", lambda request: 7)
    monkeypatch.setattr(market.deps, "personal_listings_allowed", lambda request: True)
    assert market.listing_watch_price_target_set(None, {"key": "일반매물:a", "target_manwon": 47_000}) == {
        "ok": True, "target_manwon": 47_000.0}
    assert db.listing_watch_price_targets(7) == {"일반매물:a": 47_000}
    for value in (True, 0, -1, 1_000_000_000):
        try:
            market.listing_watch_price_target_set(None, {"key": "일반매물:a", "target_manwon": value})
            assert False, "invalid target accepted"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
    try:
        market.listing_watch_price_target_set(None, {"key": "경매:a", "target_manwon": 47_000})
        assert False, "auction minimum bid cannot be a sale asking target"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    monkeypatch.setattr(market.deps, "uid", lambda request: 8)
    try:
        market.listing_watch_price_target_set(None, {"key": "일반매물:a", "target_manwon": 47_000})
        assert False, "another account can change a target"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    assert db.listing_watch_price_targets(8) == {}
    monkeypatch.setattr(market.deps, "uid", lambda request: 7)
    monkeypatch.setattr(market.deps, "personal_listings_allowed", lambda request: False)
    try:
        market.listing_watch_price_target_set(None, {"key": "일반매물:a", "target_manwon": 44_000})
        assert False, "revoked private access can set a target"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    assert market.listing_watch_price_target_remove(None, "일반매물:a") == {"ok": True}
    assert db.listing_watch_price_targets(7) == {}
