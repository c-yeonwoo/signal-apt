from realty_signal.ingest.locality import score_undervaluation


def test_undervaluation_ranks_cheap_for_locality():
    # 입지 좋은데 싼 B가 저평가 1위여야
    rows = [
        {"region": "A", "price": 10000, "accessibility": 90, "school": 90, "env": 90},  # 입지최고·비쌈
        {"region": "B", "price": 6000,  "accessibility": 85, "school": 85, "env": 85},  # 입지높은데 쌈 → 저평가
        {"region": "C", "price": 6000,  "accessibility": 20, "school": 20, "env": 20},  # 입지낮음·적정
        {"region": "D", "price": 9000,  "accessibility": 50, "school": 50, "env": 50},  # 입지중간·비쌈→고평가
    ]
    out = score_undervaluation(rows)
    assert out[0]["region"] == "B"
    assert out[0]["저평가도"] > 0
    # 입지점수는 0~100
    assert all(0 <= r["입지점수"] <= 100 for r in out)


def test_missing_inputs_do_not_become_poor_locality():
    rows = [
        {"region": "미확인", "price": 5000, "accessibility": None, "school": 0, "env": 0},
        {"region": "A", "price": 10000, "accessibility": -30, "school": 10, "env": 2},
        {"region": "B", "price": 9000, "accessibility": -40, "school": 0, "env": 0},
        {"region": "C", "price": 8000, "accessibility": -50, "school": 5, "env": 1},
    ]
    out = score_undervaluation(rows)
    assert "미확인" not in {r["region"] for r in out}
    assert "B" in {r["region"] for r in out}  # 실제 0개는 결측이 아님


def test_fewer_than_three_complete_regions_does_not_fit_model():
    rows = [
        {"region": "A", "price": 10000, "accessibility": -30, "school": 10, "env": 2},
        {"region": "B", "price": 9000, "accessibility": -40, "school": None, "env": 0},
        {"region": "C", "price": 8000, "accessibility": -50, "school": 5, "env": None},
    ]
    assert score_undervaluation(rows) == []


def test_build_skips_failed_sources_instead_of_imputing(monkeypatch):
    from realty_signal.ingest import locality as loc

    monkeypatch.setattr(loc.config, "load_env", lambda: None)
    monkeypatch.setattr(loc, "geocode", lambda region, code: (37.5, 127.0))
    monkeypatch.setattr(loc, "price_per_pyeong", lambda code, months: 5000)
    monkeypatch.setattr(loc, "transit_min", lambda lat, lng: (None, None))
    monkeypatch.setattr(loc, "school_count", lambda lat, lng: 0)
    monkeypatch.setattr(loc, "osm_environment", lambda lat, lng: {"공원": 0, "물": 0, "대형마트": 0})
    monkeypatch.setattr(loc.time, "sleep", lambda seconds: None)
    rows = loc.build_localities({"A": "1111000000"}, ["202608"])
    assert rows == []


def test_osm_error_is_not_zero_facilities(monkeypatch):
    from realty_signal.ingest import locality as loc

    monkeypatch.setattr(loc, "_OVERPASS", ["https://example.invalid"])
    monkeypatch.setattr(loc, "_fetch", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(loc.time, "sleep", lambda seconds: None)
    assert loc.osm_environment(37.5, 127.0) is None
