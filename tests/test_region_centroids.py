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
    out = api.complex_building("노원구", "__no_such_complex__")
    assert out.get("ok") is False
    assert "centroids" not in out
