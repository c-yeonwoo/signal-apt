"""매수력 API — 확정 저장 · 숏리스트 예산 연동."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from realty_signal import api as app_api
from realty_signal import auth, db
from realty_signal.services import shortlist as sl


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    monkeypatch.delenv("INVITE_CODES", raising=False)
    monkeypatch.delenv("STUDENT_ALLOWLIST", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    token, err = auth.signup("buyer@example.com", "secret1", accept_tos=True)
    assert err is None
    c = TestClient(app_api.app)
    c.cookies.set(auth.COOKIE, token)
    return c


def test_buying_power_requires_capital(client):
    d = client.get("/api/buying-power").json()
    assert d["ready"] is False
    assert d["reason"] == "no_capital"


def test_buying_power_computes_from_query(client):
    d = client.get("/api/buying-power", params={"capital": 50000, "income": 8000}).json()
    assert d["ready"] is True
    assert d["최대매수가"] > 0
    assert d["월상환"] > 0
    assert d["확정"] is None


def test_confirm_persists_to_profile(client):
    r = client.post("/api/buying-power/confirm", json={"capital": 50000, "income": 8000})
    d = r.json()
    assert d["ok"] is True
    assert d["매수력"]["최대매수가"] > 0
    assert d["매수력"]["확정일"]
    me = client.get("/api/auth/me").json()
    assert me["profile"]["매수력"]["최대매수가"] == d["매수력"]["최대매수가"]
    assert me["profile"]["가용자본"] == 50000
    # 이후 조회에 확정값이 실린다
    again = client.get("/api/buying-power").json()
    assert again["확정"] == d["매수력"]["최대매수가"]


def test_confirmed_assumptions_survive_reload(client):
    """규제지역·생애최초·금리는 프로필 필드가 없다 — 확정 가정이 유일한 기억."""
    client.post("/api/buying-power/confirm", json={
        "capital": 50000, "income": 8000, "first_time": True,
        "regulated": True, "rate": 0.045, "years": 40,
    })
    d = client.get("/api/buying-power").json()
    g = d["가정"]
    assert g["생애최초"] is True
    assert g["규제지역"] is True
    assert g["금리"] == 0.045
    assert g["만기"] == 40
    # 확정값과 재계산값이 어긋나지 않는다(= UI 가 '재확정 필요'를 띄우지 않는다)
    assert d["확정"] == d["최대매수가"]


def test_confirm_rejects_zero_capital(client):
    r = client.post("/api/buying-power/confirm", json={"capital": 0})
    assert r.status_code == 400


def test_shortlist_needs_budget(client):
    d = client.get("/api/shortlist").json()
    assert d["ready"] is False
    assert d["reason"] == "no_budget"


def test_shortlist_uses_confirmed_budget(client, monkeypatch):
    client.post("/api/buying-power/confirm", json={"capital": 50000, "income": 8000})
    monkeypatch.setattr(app_api, "_signal_map", lambda: {"노원구": "BUY"})
    monkeypatch.setattr(app_api, "_region_grades",
                        lambda r: [{"단지": "상계주공", "평단가": 2000, "급지": 3, "상위": 45}])
    monkeypatch.setattr(sl, "_locality_map", lambda: {"노원구": {"region": "노원구", "저평가도": 10}})
    monkeypatch.setattr(sl, "_region_commute", lambda region, work: None)
    d = client.get("/api/shortlist").json()
    assert d["ready"] is True
    assert d["확정예산"] is True
    assert d["candidates"][0]["단지"] == "상계주공"
    assert d["candidates"][0]["자금"]["월상환"] > 0
