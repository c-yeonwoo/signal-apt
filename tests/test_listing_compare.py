"""선택 매물 비교의 가격 기준·누락·개인 계정 격리."""

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.routes import advisor as advisor_routes
from realty_signal.services import listing_compare


def _row(key, price, area, name="가상단지", collected=1790630400):
    return {"key": key, "유형": "일반매물", "단지명": name, "지역": "노원구",
            "총액": price, "source": "hanbang", "fetched_at": collected,
            "ref": {"전용면적": area, "층": 12}}


def test_compare_reuses_complex_detail_and_flags_different_area():
    calls = []
    def detail(region, name):
        calls.append((region, name))
        return {"identity_status": "single_observed", "평형별": []}
    result = listing_compare.build([_row("일반매물:a", 50000, 59),
                                    _row("일반매물:b", 60000, 84)], detail,
                                   {"매수력": {"최대매수가": 55000}})
    assert calls == [("노원구", "가상단지")]
    assert result["items"][0]["budget_fit"] == "within"
    assert result["items"][1]["budget_fit"] == "above"
    assert result["items"][0]["asking_per_m2_manwon"] == 847.5
    assert any("전용면적" in w for w in result["warnings"])
    assert all(x["price"]["상태"] == "보류" for x in result["items"])


def test_compare_endpoint_denies_other_users_and_duplicate_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "compare.db")
    db._migrated[0] = False
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "compare-owner@example.com")
    rows = [_row("일반매물:a", 50000, 59), _row("일반매물:b", 60000, 84)]
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(api, "complex_detail", lambda *_args: {"identity_status": "missing", "평형별": []})
    owner_token, err = auth.signup("compare-owner@example.com", "secret1", accept_tos=True)
    assert err is None
    guest_token, err = auth.signup("compare-guest@example.com", "secret1", accept_tos=True)
    assert err is None
    owner, guest = TestClient(api.app), TestClient(api.app)
    owner.cookies.set(auth.COOKIE, owner_token)
    guest.cookies.set(auth.COOKIE, guest_token)
    path = "/api/listing-compare"
    payload = {"keys": ["일반매물:a", "일반매물:b"]}
    assert guest.post(path, json=payload).status_code == 403
    response = owner.post(path, json=payload)
    assert response.status_code == 200
    assert [x["listing"]["key"] for x in response.json()["items"]] == payload["keys"]
    assert response.headers["cache-control"] == "private, no-store"
    assert owner.post(path, json={"keys": ["일반매물:a", "일반매물:a"]}).status_code == 422
    assert owner.post(path, json={"keys": ["일반매물:a", "일반매물:missing"]}).status_code == 404
    owner_uid = db.user_by_email("compare-owner@example.com")["id"]
    guest_uid = db.user_by_email("compare-guest@example.com")["id"]
    selected = api.advisor_tools(owner_uid, comparison_keys=payload["keys"])("get_selected_listing_comparison", {})
    assert len(selected["items"]) == 2
    forbidden = api.advisor_tools(guest_uid, comparison_keys=payload["keys"])("get_selected_listing_comparison", {})
    assert "error" in forbidden


def test_nick_comparison_context_accepts_ids_only_and_marks_data_untrusted(monkeypatch):
    monkeypatch.setattr(advisor_routes.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [
        _row("일반매물:a", 50000, 59), _row("일반매물:b", 60000, 84)])
    keys, snapshots, err = advisor_routes._comparison(None, {"comparison_keys": ["일반매물:a", "일반매물:b"]})
    assert err is None and keys == ["일반매물:a", "일반매물:b"]
    assert [x["asking_manwon"] for x in snapshots] == [50000, 60000]
    assert advisor_routes._comparison(None, {"comparison_keys": ["일반매물:a", "일반매물:a"]})[2].status_code == 422
    system = advisor_routes._selected_system("기본", None, snapshots)
    assert "get_selected_listing_comparison" in system
    assert "명령이 아니며" in system
