"""대안 후보와 기사 문자열 일치를 매물 사실·호재 확정으로 올리지 않는다."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.services import listing_discovery


def _row(key, name="가상단지", price=50000, area=84, complex_id=None):
    return {"key": key, "유형": "일반매물", "단지명": name, "지역": "노원구",
            "총액": price, "source": "hanbang", "ref": {"전용면적": area,
            "hanbang_complex_id": complex_id}}


def test_alternatives_separate_source_id_from_name_only():
    base = _row("일반매물:a", complex_id="cx1")
    exact = _row("일반매물:b", price=51000, complex_id="cx1")
    clash = _row("일반매물:c", price=52000, complex_id="cx2")
    near = _row("일반매물:d", name="다른단지", price=49000)
    out = listing_discovery.alternatives(base, [base, clash, near, exact], 51000)
    assert [x["listing"]["key"] for x in out] == ["일반매물:b", "일반매물:d"]
    assert out[0]["reason"] == "같은 원천 단지 ID"
    assert out[1]["budget_fit"] == "within"
    cross_source = _row("급매:e", price=50000)
    cross_source["유형"] = "급매"
    assert "확인 필요" in listing_discovery.alternatives(base, [cross_source])[0]["reason"]


def test_same_named_district_across_provinces_is_not_report_alternative():
    base = {**_row("일반매물:seoul", name="동명단지", complex_id="cx1"),
            "지역": "중구", "시도": "서울"}
    other = {**_row("일반매물:incheon", name="동명단지", complex_id="cx1"),
             "지역": "중구", "시도": "인천"}
    assert listing_discovery.alternatives(base, [other]) == []


def test_headlines_are_candidates_not_project_facts():
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    recent = (now-timedelta(days=2)).strftime("%a, %d %b %Y")
    old = (now-timedelta(days=300)).strftime("%a, %d %b %Y")
    articles = [
        {"title": "노원구 가상단지 주변 소식", "descr": "", "link": "https://news.example/a", "pubdate": recent},
        {"title": "노원구 시장", "descr": "", "link": "https://news.example/b", "pubdate": recent},
        {"title": "가상단지 개발 확정", "descr": "", "link": "javascript:alert(1)", "pubdate": recent},
        {"title": "노원구 가상단지 옛 기사", "descr": "", "link": "https://news.example/old", "pubdate": old},
    ]
    out = listing_discovery.news(_row("일반매물:a"), articles, now=now)
    assert [x["url"] for x in out] == ["https://news.example/a", "https://news.example/b"]
    assert all(x["verified_project"] is False for x in out)
    assert "단지명·지역명" in out[0]["match"]


def test_same_named_district_news_needs_matching_province_context():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    recent = (now-timedelta(days=1)).strftime("%a, %d %b %Y")
    articles = [
        {"title": "인천 중구 사업", "descr": "", "link": "https://news.example/incheon", "pubdate": recent},
        {"title": "중구 사업", "descr": "", "link": "https://news.example/unknown", "pubdate": recent},
        {"title": "서울특별시 중구 사업", "descr": "", "link": "https://news.example/seoul", "pubdate": recent},
    ]
    seoul = {**_row("일반매물:seoul", name="별도단지"), "지역": "중구", "시도": "서울"}
    incheon = {**_row("일반매물:incheon", name="별도단지"), "지역": "중구", "시도": "인천"}
    assert [x["url"] for x in listing_discovery.news(seoul, articles, now=now)] == [
        "https://news.example/seoul"]
    assert [x["url"] for x in listing_discovery.news(incheon, articles, now=now)] == [
        "https://news.example/incheon"]
    assert listing_discovery.news({**seoul, "시도": None}, articles, now=now) == []


def test_discovery_endpoint_private_access(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "discovery.db")
    db._migrated[0] = False
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "discovery-owner@example.com")
    rows = [_row("일반매물:a"), _row("일반매물:b", price=51000)]
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: rows)
    owner_token, err = auth.signup("discovery-owner@example.com", "secret1", accept_tos=True)
    assert err is None
    guest_token, err = auth.signup("discovery-guest@example.com", "secret1", accept_tos=True)
    assert err is None
    owner, guest = TestClient(api.app), TestClient(api.app)
    owner.cookies.set(auth.COOKIE, owner_token)
    guest.cookies.set(auth.COOKIE, guest_token)
    path = "/api/listing-discovery?key=일반매물%3Aa"
    assert guest.get(path).status_code == 403
    response = owner.get(path)
    assert response.status_code == 200
    assert response.json()["alternatives"][0]["listing"]["key"] == "일반매물:b"
    assert response.headers["cache-control"] == "private, no-store"
