"""GTX map evidence stays source-linked and distinct from operating service."""

from datetime import date
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from realty_signal import api


def test_gtx_evidence_is_small_cached_and_source_linked():
    response = TestClient(api.app).get("/assets/gtx-evidence.json")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert int(response.headers["content-length"]) < 10_000
    data = response.json()
    assert date.fromisoformat(data["asof"]) <= date.today()
    assert "실제 철도 선형" in data["geometry_note"]
    assert "도보거리" in data["geometry_note"]
    assert "출입구" in data["coordinate_note"]
    assert all(urlparse(url).hostname == "www.molit.go.kr"
               for key, url in data["sources"].items() if key != "coordinates")
    assert urlparse(data["sources"]["coordinates"]).hostname == "www.openstreetmap.org"


def test_gtx_sections_never_present_future_service_as_open():
    data = TestClient(api.app).get("/assets/gtx-evidence.json").json()
    assert {section["line"] for section in data["sections"]} == {"A", "B", "C"}
    assert {(section["line"], section["state"]) for section in data["sections"]} == {
        ("A", "operating"), ("A", "not_operating"),
        ("B", "under_construction"), ("C", "under_construction"),
    }
    assert any(section["station_names"] == ["서울", "삼성", "수서"]
               and section["state"] == "not_operating" for section in data["sections"])
    for section in data["sections"]:
        assert section["source"] in data["sources"]
        assert len(section["station_names"]) >= 2
        for name in section["station_names"]:
            lat, lon = data["stations"][name]
            assert 36 < lat < 39 and 125 < lon < 129
