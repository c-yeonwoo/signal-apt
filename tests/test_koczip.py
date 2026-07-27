"""콕집 클라이언트·DB 헬퍼 (네트워크 호출 없음)."""

from realty_signal import db
from realty_signal.ingest import koczip as kz
from realty_signal.routes import koczip as kz_routes


def test_scan_regions_mocked(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False
    monkeypatch.setattr(kz, "SLEEP_SEC", 0)
    monkeypatch.setattr(kz, "MAX_COMPLEX_PER_REGION", 5)

    def fake_bounds(swlat, swlng, nelat, nelng, limit=300):
        return {"items": [{
            "complex_no": "203", "name": "상계주공2단지",
            "lat": 37.65, "lng": 127.06, "listings": 40, "c_sale": 20,
        }]}

    def fake_summary(cno):
        return {
            "complex_no": cno, "complex_name": "상계주공2단지",
            "region": "서울시 노원구 상계동",
            "latitude": 37.65, "longitude": 127.06,
            "by_type": [{"sale_count": 2, "sale_min": 500_000_000, "sale_max": 600_000_000}],
            "listing_counts": {"A1": 2, "total": 10},
        }

    def fake_qd(cno, min_discount=0.0):
        return {"items": [{
            "article_no": "ART1", "price": 500_000_000, "discount": -0.15,
            "area_name": "54A", "floor_info": "4/15", "direction": "남향",
            "naver_url": "https://example.com/a", "realtor_name": "테스트",
        }]}

    def fake_special(**kw):
        return {"items": [{
            "article_no": "SP1", "complex_no": "203", "complex_name": "상계주공2단지",
            "region_name": "서울시 노원구 상계동", "price": 480_000_000,
            "area_name": "54A", "floor_info": "3/15", "direction": "동향",
            "naver_url": "https://example.com/s", "matched": "특가",
        }]}

    monkeypatch.setattr(kz, "fetch_complexes_in_bounds", fake_bounds)
    monkeypatch.setattr(kz, "fetch_complex_summary", fake_summary)
    monkeypatch.setattr(kz, "fetch_complex_quick_deals", fake_qd)
    monkeypatch.setattr(kz, "fetch_special_deals", fake_special)

    stats = kz.scan_regions(
        ["노원구"],
        centroid_fn=lambda r: (37.65, 127.06),
        signal_map={"노원구": "STRONG_BUY"},
    )
    assert stats["complexes"] == 1
    assert stats["articles_discount"] == 1
    assert stats["articles_special"] == 1
    arts = db.koczip_article_list(region="노원구")
    assert {a["kind"] for a in arts} == {"discount", "special"}
    rows = [kz_routes._norm_article(a) for a in arts]
    assert any(r["출처"] == "콕집" and r["유형"] == "할인" for r in rows)
    assert any(r["유형"] == "특가" for r in rows)


def test_koczip_stale_matches_radar_ttl(tmp_path, monkeypatch):
    """바로집(_RADAR_MAX_AGE=1일)과 동일 기준으로 stale 판정."""
    import time
    from realty_signal import api as app_api

    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False
    assert app_api._koczip_stale() is True
    now = int(time.time())
    db.koczip_complex_upsert({
        "complex_no": "1", "region": "노원구", "name": "T",
        "lat": 37.0, "lng": 127.0, "sale_count": 1,
        "sale_min": 1, "sale_max": 2, "listing_total": 1,
        "signal": "BUY", "raw": "{}", "ts": now,
    })
    assert app_api._koczip_stale() is False
    db.koczip_complex_upsert({
        "complex_no": "1", "region": "노원구", "name": "T",
        "lat": 37.0, "lng": 127.0, "sale_count": 1,
        "sale_min": 1, "sale_max": 2, "listing_total": 1,
        "signal": "BUY", "raw": "{}",
        "ts": now - app_api._RADAR_MAX_AGE - 10,
    })
    assert app_api._koczip_stale() is True


def test_buyer_discount_pct_from_asking_below_real():
    assert kz.buyer_discount_pct({"discount_min": -0.269}) == 26.9
    assert kz.buyer_discount_pct({"discount_min": 0.1}) == -10.0
    assert kz.buyer_discount_pct({}) is None


def test_article_discount_pct():
    assert kz.article_discount_pct({"discount": -0.2}) == 20.0
    assert kz.article_discount_pct({}) is None


def test_region_candidates_seoul_gu():
    c = kz.region_candidates("서울시 노원구 상계동")
    assert "노원구" in c
    assert "서울시 노원구" in c


def test_region_candidates_suwon():
    c = kz.region_candidates("경기도 수원시 권선구 세류동")
    assert "수원시 권선구" in c
    assert "권선구" in c


def test_region_matches():
    assert kz.region_matches("노원구", "서울시 노원구 상계동")
    assert kz.region_matches("수원시 권선구", "경기도 수원시 권선구 세류동")
    assert not kz.region_matches("강남구", "서울시 노원구 상계동")


def test_sale_band_from_summary():
    sm = {"by_type": [
        {"sale_count": 2, "sale_min": 500_000_000, "sale_max": 600_000_000},
        {"sale_count": 1, "sale_min": 450_000_000, "sale_max": 450_000_000},
        {"sale_count": 0, "sale_min": 999_000_000, "sale_max": 999_000_000},
    ]}
    smin, smax, cnt = kz.sale_band_from_summary(sm)
    assert cnt == 3
    assert smin == 45_000
    assert smax == 60_000


def test_db_upsert_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False
    db.koczip_complex_upsert({
        "complex_no": "203", "region": "노원구", "name": "상계주공2단지",
        "lat": 37.65, "lng": 127.06, "sale_count": 10,
        "sale_min": 50000, "sale_max": 60000, "listing_total": 20,
        "signal": "STRONG_BUY", "raw": "{}", "ts": 1_700_000_000,
    })
    db.koczip_article_upsert({
        "article_no": "A1", "complex_no": "203", "region": "노원구",
        "kind": "discount", "name": "상계주공2단지", "price": 50000,
        "area": "54", "floor": "4/5", "direction": "남향",
        "discount_pct": 20.0, "naver_url": "https://example.com",
        "realtor": "테스트", "matched": None,
        "lat": 37.65, "lng": 127.06, "signal": "STRONG_BUY", "ts": 1_700_000_000,
    })
    cxs = db.koczip_complex_list(region="노원구")
    arts = db.koczip_article_list(region="노원구", kind="discount")
    assert len(cxs) == 1 and cxs[0]["name"] == "상계주공2단지"
    assert len(arts) == 1 and arts[0]["discount_pct"] == 20.0
    n = kz_routes._norm_article(arts[0])
    assert n["출처"] == "콕집" and n["급매갭"] == -20.0
    c = kz_routes._norm_complex(cxs[0])
    assert c["유형"] == "호가요약" and "naver.com" in (c["naver_url"] or "")
    db.koczip_clear_region("노원구")
    assert db.koczip_complex_list(region="노원구") == []
    assert db.koczip_article_list(region="노원구") == []
