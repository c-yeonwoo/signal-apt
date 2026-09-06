"""콕집 클라이언트·DB 헬퍼 (네트워크 호출 없음)."""

import json
import pathlib

import pytest

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



def test_koczip_auto_refresh_is_weekly_not_daily(tmp_path, monkeypatch):
    """자동 수집은 주 1회. 수동 기준(1일)과 다른 임계값을 쓴다."""
    import time
    from realty_signal import api as app_api

    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False

    def _seed(age_sec):
        db.koczip_complex_upsert({
            "complex_no": "1", "region": "노원구", "name": "T",
            "lat": 37.0, "lng": 127.0, "sale_count": 1,
            "sale_min": 1, "sale_max": 2, "listing_total": 1,
            "signal": "BUY", "raw": "{}", "ts": int(time.time()) - age_sec,
        })

    # 비어 있으면 자동 수집하지 않는다 — 첫 수집은 admin 이 시작한다
    assert app_api._koczip_auto_due() is False
    assert app_api._koczip_stale() is True      # 화면 표시는 '갱신 필요'

    _seed(2 * 86400)                            # 2일 — 수동 기준은 만료, 자동은 아직
    assert app_api._koczip_stale() is True
    assert app_api._koczip_auto_due() is False

    _seed(8 * 86400)                            # 8일 — 자동 수집 대상
    assert app_api._koczip_auto_due() is True



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

def test_koczip_collection_is_permanently_disabled():
    """koczip 수집은 영구 중단이다. **네트워크 호출이 아예 나가면 안 된다.**

    2026-09-06: 운영자가 전 지역 요청에 403 과 함께
    "무단 크롤링·스크래핑·복제·저장·재배포 금지" 를 명시했다.
    기술적 차단과 권리 유보 고지가 동시에 있었으므로 재시도·우회 대상이 아니다.
    """
    import urllib.request

    assert kz.DISABLED is True
    called = []
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: called.append(1)
    try:
        for fn in (lambda: kz.fetch_complexes_in_bounds(37.4, 127.0, 37.6, 127.1),
                   lambda: kz.fetch_complex_summary("203"),
                   lambda: kz.fetch_complex_quick_deals("203"),
                   lambda: kz.fetch_quick_deals(),
                   lambda: kz.fetch_special_deals()):
            with pytest.raises(kz.KoczipDisabled):
                fn()
    finally:
        urllib.request.urlopen = orig
    assert called == [], "차단 상태인데 외부 요청이 나갔다"


def test_disabled_error_is_not_a_retryable_error():
    """영구 중단은 일시적 오류와 구분돼야 한다 — 재시도 루프에 걸리면 안 된다."""
    assert issubclass(kz.KoczipDisabled, kz.KoczipError)
    assert kz.KoczipDisabled is not kz.KoczipError


def test_browser_impersonating_headers_are_gone():
    """명시적 거부 이후 브라우저 위장 헤더는 남겨두면 안 된다."""
    raw = pathlib.Path("src/realty_signal/ingest/koczip.py").read_text(encoding="utf-8")
    # 주석에는 "예전에 위장했었다" 는 기록이 남아 있다 — **코드 줄만** 본다
    code = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    assert "_HDR" not in code
    assert "Mozilla/5.0" not in code
    assert "Referer" not in code
    assert "Origin" not in code


def test_no_automatic_collection_call_sites_remain():
    """부팅 시딩·주간 루프에서 수집 호출이 사라져야 한다."""
    import inspect

    from realty_signal import api as app_api

    for fn in (app_api._seed_if_missing, app_api._auto_refresh_loop):
        assert "koczip_refresh" not in inspect.getsource(fn), f"{fn.__name__} 에 수집 호출이 남아 있다"


def test_all_koczip_routes_are_closed():
    """수집만 막고 저장분을 계속 보여주면 **재배포**가 이어진다. 조회도 닫는다.

    인증 미들웨어를 거치지 않고 라우트 본체를 직접 부른다 —
    다른 테스트가 db.DB 를 임시 경로로 바꿔 세션이 안 붙기 때문이다.
    """
    req = object()
    for name, call in (
        ("listings", lambda: kz_routes.koczip_listings(req)),
        ("quick-deals", lambda: kz_routes.quick_deals(req)),
        ("scan", lambda: kz_routes.koczip_scan(req, {})),
    ):
        r = call()
        assert getattr(r, "status_code", None) == 410, f"{name} 이 닫히지 않았다"
        assert json.loads(bytes(r.body)).get("error") == "koczip_disabled"

    meta = kz_routes.koczip_meta(req)
    assert meta.get("allowed") is False, "메타가 allowed=True 면 화면에 탭이 남는다"
    assert meta.get("disabled") is True

