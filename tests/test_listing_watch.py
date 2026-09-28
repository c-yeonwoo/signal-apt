"""매물 찜: ID 안정성, 가격·청약 일정, 대안, 개인용 원천 격리."""

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.services import listing_watch as watch


def _row(key, name="A", price=50_000, *, kind="급매", region="노원구", complex_no="1"):
    return {"key": key, "유형": kind, "단지명": name, "지역": region,
            "총액": price, "평형": 25, "ref": {"complex_no": complex_no}}


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
