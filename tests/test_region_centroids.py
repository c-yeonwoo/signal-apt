"""region-centroids 엔드포인트 — 청약 지도 핀 폴백용."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from realty_signal import api


def test_region_centroids_route_returns_known_metro():
    client = TestClient(api.app)
    with patch("realty_signal.routes.deps.uid", return_value=1), \
         patch.object(api, "_uid", return_value=1):
        r = client.get("/api/region-centroids?regions=노원구,강남구")
    assert r.status_code == 200
    cen = r.json().get("centroids") or {}
    assert "노원구" in cen
    assert len(cen["노원구"]) == 2


def test_complex_building_not_hijacked_by_centroids_body():
    """regression: region_centroids body was accidentally left under complex_building."""
    # The route shape is under test; a developer's .env must not trigger a live lookup.
    with patch("realty_signal.personal_layer.building_for_complex", return_value=None):
        out = api.complex_building("노원구", "__no_such_complex__")
    assert out.get("ok") is False
    assert "centroids" not in out


def test_verified_code_centroid_is_keyed_by_ref_and_ambiguous_name_is_skipped(monkeypatch):
    identity = {"name": "중구", "sido": "서울", "code": "1114000000",
                "region_id": "kb:1114000000"}
    monkeypatch.setattr(api.md, "current_region_identity", lambda ref: identity if ref == identity["region_id"] else None)
    calls = []
    monkeypatch.setattr(api, "_region_centroid", lambda name, code: calls.append((name, code)) or (37.56, 126.99))
    result = api.region_centroids("kb:1114000000,중구,kb:9999999999")
    assert result["centroids"] == {"kb:1114000000": [37.56, 126.99]}
    assert result["identities"]["kb:1114000000"] == {
        "name": "중구", "sido": "서울", "region_id": "kb:1114000000"}
    assert calls == [("중구", "1114000000")]
