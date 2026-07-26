"""임장 API — 코스 생성 · 방문 기록 저장/조회/삭제."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from realty_signal import api as app_api
from realty_signal import auth, db
from realty_signal.services import imjang as ij
from realty_signal.services import shortlist as sl

GRADES = {
    "노원구": [{"단지": "상계주공7", "평단가": 3000, "급지": 1, "상위": 10},
             {"단지": "중계그린", "평단가": 2800, "급지": 2, "상위": 30}],
    "금천구": [{"단지": "독산한양", "평단가": 2600, "급지": 1, "상위": 15}],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    for k in ("INVITE_CODES", "STUDENT_ALLOWLIST", "RAILWAY_ENVIRONMENT", "APP_ENV"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(app_api, "_signal_map", lambda: {"노원구": "BUY", "금천구": "BUY"})
    monkeypatch.setattr(app_api, "_region_grades", lambda r: GRADES.get(r, []))
    monkeypatch.setattr(sl, "_locality_map",
                        lambda: {"노원구": {"region": "노원구", "저평가도": 10},
                                 "금천구": {"region": "금천구", "저평가도": 8}})
    monkeypatch.setattr(sl, "_region_commute", lambda region, work: None)
    monkeypatch.setattr(ij, "_coords", lambda cands: {
        f"{c['region']} {c['단지']}": (37.6, 127.0) for c in cands})
    monkeypatch.setattr(ij, "_move_min", lambda a, b: 20)
    token, err = auth.signup("walker@example.com", "secret1", accept_tos=True)
    assert err is None
    uid = db.session_user(token)["id"]
    db.profile_set(uid, {"가용자본": 50000, "연소득": 8000,
                         "매수력": {"최대매수가": 90000}, "관심평수": "84"})
    c = TestClient(app_api.app)
    c.cookies.set(auth.COOKIE, token)
    c.uid = uid
    return c


def test_course_needs_candidates(client):
    db.profile_set(client.uid, {})
    d = client.get("/api/imjang/course").json()
    assert d["ready"] is False


def test_course_returns_timed_stops(client):
    d = client.get("/api/imjang/course", params={"on": "2026-08-01", "start": "09:30"}).json()
    assert d["ready"] is True
    assert d["stops"][0]["도착"] == "09:30"
    assert all(s["지도"].startswith("https://map.kakao.com/") for s in d["stops"])
    assert len(d["checks"]) == len(ij.CHECKS)


def test_visit_save_scores_and_lists(client):
    r = client.post("/api/imjang/visit", json={
        "region": "노원구", "단지": "상계주공7", "방문일": "2026-08-01",
        "checks": {"walk": 2, "noise": 0}, "verdict": "보류", "memo": "큰길 소음"}).json()
    assert r["ok"] and r["점수"]["점수"] == 50
    assert r["약점"] == ["소음·냄새"]
    v = client.get("/api/imjang/visits").json()["visits"]
    assert len(v) == 1 and v[0]["verdict"] == "보류" and v[0]["memo"] == "큰길 소음"


def test_same_complex_same_day_overwrites(client):
    body = {"region": "노원구", "단지": "상계주공7", "방문일": "2026-08-01"}
    client.post("/api/imjang/visit", json={**body, "checks": {"walk": 0}})
    client.post("/api/imjang/visit", json={**body, "checks": {"walk": 2}, "memo": "다시 봄"})
    v = client.get("/api/imjang/visits").json()["visits"]
    assert len(v) == 1 and v[0]["score"] == 100 and v[0]["memo"] == "다시 봄"


def test_visit_requires_region_and_complex(client):
    r = client.post("/api/imjang/visit", json={"region": "노원구"})
    assert r.status_code == 400


def test_unknown_verdict_is_dropped(client):
    r = client.post("/api/imjang/visit", json={
        "region": "노원구", "단지": "상계주공7", "verdict": "매수확정"}).json()
    assert r["ok"]
    assert client.get("/api/imjang/visits").json()["visits"][0]["verdict"] == ""


def test_visited_complex_moves_to_end_of_course(client):
    first = client.get("/api/imjang/course", params={"on": "2026-08-01"}).json()
    head = first["stops"][0]["단지"]
    client.post("/api/imjang/visit", json={
        "region": first["stops"][0]["region"], "단지": head,
        "방문일": "2026-07-30", "checks": {"walk": 2}})
    again = client.get("/api/imjang/course", params={"on": "2026-08-01"}).json()
    assert again["stops"][-1]["단지"] == head
    assert again["stops"][-1]["방문일"]["방문일"] == "2026-07-30"


def test_delete_visit(client):
    client.post("/api/imjang/visit", json={"region": "노원구", "단지": "상계주공7"})
    vid = client.get("/api/imjang/visits").json()["visits"][0]["id"]
    assert client.request("DELETE", f"/api/imjang/visit/{vid}").json()["ok"] is True
    assert client.get("/api/imjang/visits").json()["visits"] == []


def test_other_user_cannot_delete(client, monkeypatch):
    client.post("/api/imjang/visit", json={"region": "노원구", "단지": "상계주공7"})
    vid = client.get("/api/imjang/visits").json()["visits"][0]["id"]
    tok, _ = auth.signup("stranger@example.com", "secret1", accept_tos=True)
    other = TestClient(app_api.app)
    other.cookies.set(auth.COOKIE, tok)
    assert other.request("DELETE", f"/api/imjang/visit/{vid}").json()["ok"] is False
    assert len(client.get("/api/imjang/visits").json()["visits"]) == 1
