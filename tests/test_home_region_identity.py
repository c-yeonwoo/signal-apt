"""Residence personalization must never guess across duplicate district names."""

import json
from types import SimpleNamespace
import urllib.request

from realty_signal import api


SEOUL = {"region_id": "kb:1114000000", "code": "1114000000", "name": "중구", "sido": "서울"}
BUSAN = {"region_id": "kb:2611000000", "code": "2611000000", "name": "부산 중구", "sido": "부산"}


def _identities(monkeypatch):
    identities = {x["region_id"]: x for x in (SEOUL, BUSAN)}
    monkeypatch.setattr(api.md, "current_region_identity", identities.get)
    monkeypatch.setattr(api.md, "code_of", lambda name: {
        "중구": SEOUL["code"], "부산 중구": BUSAN["code"]}.get(name, ""))


def test_presale_residence_requires_verified_code_and_matching_province(monkeypatch):
    _identities(monkeypatch)
    rows = [
        {"단지명": "부산", "지역": "중구", "시도": "부산", "주소": "부산 중구 서울빌딩",
         "상태": "접수중", "Dday": 0, "시그널": "WATCH"},
        {"단지명": "서울", "지역": "중구", "시도": "서울", "주소": "서울 중구",
         "상태": "접수중", "Dday": 0, "시그널": "WATCH"},
    ]
    monkeypatch.setattr(api, "_presale_visible_items", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(api, "_uid", lambda request: 7)
    profile = {"거주지": "중구", "거주지코드": SEOUL["region_id"]}
    monkeypatch.setattr(api.db, "profile_get", lambda uid: profile)

    result = api.presale_list(object())
    assert [r["단지명"] for r in result] == ["서울", "부산"]
    assert [r["거주지일치"] for r in result] == [True, False]
    assert all("당해" not in r for r in result)

    profile["거주지코드"] = BUSAN["region_id"]  # Mismatched saved name and code.
    assert all(not r["거주지일치"] for r in api.presale_list(object()))
    profile.pop("거주지코드")  # Ambiguous legacy name also remains unverified.
    assert all(not r["거주지일치"] for r in api.presale_list(object()))


def test_legacy_unique_residence_requires_current_identity(monkeypatch):
    identity = {"region_id": "kb:1135000000", "code": "1135000000",
                "name": "노원구", "sido": "서울"}
    monkeypatch.setattr(api.md, "code_of", lambda name: identity["code"] if name == "노원구" else "")
    monkeypatch.setattr(api.md, "current_region_identity", lambda ref: identity if ref == identity["region_id"] else None)
    assert api._verified_home_region({"거주지": "노원구"}) == identity
    assert api._verified_home_region({"거주지": "노원구", "거주지코드": "kb:9999999999"}) is None


def test_address_search_rejects_cross_province_and_ambiguous_matches(monkeypatch):
    _identities(monkeypatch)
    monkeypatch.setattr(api, "_kb", lambda: SimpleNamespace(codes={
        "중구": SEOUL["code"], "부산 중구": BUSAN["code"]}))
    assert api._address_region_identity("서울특별시 중구 다동") == SEOUL
    assert api._address_region_identity("부산광역시 중구 중앙동") == BUSAN
    assert api._address_region_identity("중구 중앙동") is None
    assert api._address_region_identity("서울특별시 강남구 부산 중구 식당") is None


def test_address_search_keeps_unverified_place_for_work_but_not_residence(monkeypatch):
    _identities(monkeypatch)
    monkeypatch.setattr(api, "_kb", lambda: SimpleNamespace(codes={"중구": SEOUL["code"]}))
    monkeypatch.setattr(api.config, "kakao_key", lambda: "synthetic-key")
    body = json.dumps({"documents": [
        {"place_name": "시청", "road_address_name": "서울특별시 중구 다동"},
        {"place_name": "외부 직장", "address_name": "미확인 직장 주소"},
    ]}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: SimpleNamespace(read=lambda: body))

    rows = api.addr_search("시청")["results"]
    assert rows[0]["region_id"] == SEOUL["region_id"]
    assert rows[0]["sigungu"] == "중구"
    assert rows[1]["name"] == "외부 직장"
    assert rows[1]["region_id"] is None
