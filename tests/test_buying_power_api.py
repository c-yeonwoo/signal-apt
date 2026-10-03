"""매수력 API — 확정 저장 · 숏리스트 예산 연동."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from realty_signal import api as app_api
from realty_signal import auth, db
from realty_signal.services import market_data as md
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


def test_explicit_null_clears_monthly_budget_but_omission_preserves_it(client):
    client.post("/api/buying-power/confirm", json={"capital": 50000, "income": 8000, "monthly_budget": 100})
    saved = client.post("/api/buying-power/confirm", json={"capital": 50000}).json()
    assert saved["매수력"]["가정"]["월상환한도"] == 100
    cleared = client.post("/api/buying-power/confirm", json={"monthly_budget": None}).json()
    assert cleared["매수력"]["가정"]["월상환한도"] is None
    assert client.get("/api/buying-power").json()["가정"]["월상환한도"] is None


def test_clearing_monthly_budget_preview_matches_save_without_mutation(client):
    client.post("/api/buying-power/confirm", json={"capital": 50000, "income": 8000, "monthly_budget": 100})
    preview = client.get("/api/buying-power?clear_monthly_budget=true").json()
    assert preview["가정"]["월상환한도"] is None
    assert preview["재확인필요"] is True
    assert client.get("/api/buying-power").json()["가정"]["월상환한도"] == 100
    saved = client.post("/api/buying-power/confirm", json={"monthly_budget": None}).json()
    assert preview["가정버전"] == saved["매수력"]["가정버전"]


def test_changed_assumption_requires_reconfirmation_even_if_maximum_is_equal(client):
    client.post("/api/buying-power/confirm", json={"capital": 50000, "income": 8000})
    assert client.get("/api/buying-power").json()["재확인필요"] is False
    result = client.get("/api/buying-power?monthly_budget=100000").json()
    assert result["재확인필요"] is True


def test_saved_ceiling_drift_requires_reconfirmation_below_old_ui_tolerance(client):
    confirmed = client.post("/api/buying-power/confirm", json={
        "capital": 50000, "income": 8000}).json()["매수력"]
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    profile = db.profile_get(uid)
    profile["매수력"]["최대매수가"] = confirmed["최대매수가"] + 100
    db.profile_set(uid, profile)
    statement = client.get("/api/buying-power").json()
    assert statement["확정"] != statement["최대매수가"]
    assert statement["재확인필요"] is True


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


def test_region_query_applies_current_regulation(client, monkeypatch):
    source = SimpleNamespace(codes={"강남구": "1168000000"},
                             regions=["강남구"], identity_verified=True)
    monkeypatch.setattr(md, "kb", lambda: source)
    seoul = client.get("/api/buying-power",
                       params={"capital": 80000, "income": 15000, "first_time": True,
                                   "region": "강남구"}).json()
    assert seoul["지역식별"] == "verified"
    assert seoul["가정"]["지역코드"] == "kb:1168000000"
    assert seoul["규제"]["규제지역"] is True
    assert seoul["규제"]["절대한도"] == 60_000        # 시가 15억 이하 → 6억
    assert seoul["규제"]["적용만기"] == 30
    assert seoul["LTV상한"] == 0.70
    local = client.get("/api/buying-power",
                       params={"capital": 80000, "income": 15000, "first_time": True,
                                   "region": "해운대구"}).json()
    assert local["지역식별"] == "unverified_name"
    assert local["가정"]["지역"] is None
    assert local["규제"]["지역가정"] is True
    assert local["LTV상한"] == 0.70
    assert client.post("/api/buying-power/confirm", json={
        "capital": 80000, "income": 15000, "region": "해운대구"}).status_code == 422


def test_confirm_remembers_region(client, monkeypatch):
    source = SimpleNamespace(codes={"성남시 분당구": "4113500000"},
                             regions=["성남시 분당구"], identity_verified=True)
    monkeypatch.setattr(md, "kb", lambda: source)
    saved = client.post("/api/buying-power/confirm", json={
        "capital": 50000, "income": 9000, "region": "성남시 분당구",
        "region_code": "kb:4113500000"})
    assert saved.status_code == 200
    d = client.get("/api/buying-power").json()
    assert d["가정"]["지역"] == "성남시 분당구"
    assert d["가정"]["지역코드"] == "kb:4113500000"
    assert d["규제"]["규제지역"] is True
    assert d["확정"] == d["최대매수가"]

    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    profile = db.profile_get(uid)
    assert app_api.buying_power.validated_confirmed_power(profile) is not None
    profile["매수지역코드"] = "kb:1168000000"
    assert app_api.buying_power.validated_confirmed_power(profile) is None


def test_ambiguous_jung_gu_requires_verified_code_for_finance(client, monkeypatch):
    source = SimpleNamespace(codes={"중구": "1114000000", "강남구": "1168000000"},
                             regions=["중구", "강남구"], identity_verified=True)
    monkeypatch.setattr(md, "kb", lambda: source)
    params = {"capital": 50000, "income": 9000, "region": "중구"}
    assert client.get("/api/buying-power", params=params).status_code == 422
    assert client.post("/api/buying-power/confirm", json=params).status_code == 422

    selected = {**params, "region_code": "kb:1114000000"}
    preview = client.get("/api/buying-power", params=selected).json()
    assert preview["지역식별"] == "verified"
    assert preview["가정"]["지역"] == "중구"
    assert preview["가정"]["지역코드"] == "kb:1114000000"
    saved = client.post("/api/buying-power/confirm", json=selected).json()
    assert saved["ok"] is True
    assert saved["매수력"]["가정"]["지역코드"] == "kb:1114000000"
    assert client.get("/api/buying-power").json()["재확인필요"] is False

    mismatched = {**selected, "region": "인천 중구"}
    assert client.get("/api/buying-power", params=mismatched).status_code == 422
    assert client.post("/api/buying-power/confirm", json=mismatched).status_code == 422


def test_old_or_unverified_profile_region_uses_conservative_assumption(client, monkeypatch):
    source = SimpleNamespace(codes={"중구": "1114000000"},
                             regions=["중구"], identity_verified=True)
    monkeypatch.setattr(md, "kb", lambda: source)
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.profile_set(uid, {"가용자본": 50000, "연소득": 9000, "매수지역": "중구",
                         "매수력": {"가정": {"지역": "중구"}}})
    old = client.get("/api/buying-power").json()
    assert old["가정"]["지역"] is None
    assert old["규제"]["지역가정"] is True
    assert old["지역식별"] == "reselection_required"

    db.profile_set(uid, {"가용자본": 50000, "연소득": 9000, "매수지역": "중구",
                         "매수력": {"가정": {"지역": "중구", "지역코드": "kb:1114000000"}}})
    source.identity_verified = False
    unverified = client.get("/api/buying-power").json()
    assert unverified["가정"]["지역"] is None
    assert unverified["지역식별"] == "reselection_required"


def test_direct_profile_consumers_never_reuse_unverified_region(client, monkeypatch):
    source = SimpleNamespace(codes={"강남구": "1168000000"},
                             regions=["강남구"], identity_verified=True)
    monkeypatch.setattr(md, "kb", lambda: source)
    for profile in (
        {"가용자본": 50000, "매수지역": "해운대구"},
        {"가용자본": 50000, "매수지역": "강남구", "매수지역코드": "kb:2811000000"},
    ):
        params = app_api.buying_power.params_from_profile(profile)
        assert params.region is None
        assert params.sido is None
        assert params.regulated is True
        assert params.metro is True


def test_finance_region_override_does_not_keep_previous_province(client, monkeypatch):
    source = SimpleNamespace(codes={"강남구": "1168000000"},
                             regions=["강남구"], identity_verified=True)
    monkeypatch.setattr(md, "kb", lambda: source)
    saved = client.post("/api/buying-power/confirm", json={
        "capital": 80000, "income": 15000, "region": "강남구",
        "region_code": "kb:1168000000"})
    assert saved.status_code == 200
    local = client.get("/api/buying-power", params={"region": "해운대구"}).json()
    assert local["가정"]["지역"] is None
    assert local["지역식별"] == "unverified_name"
    assert local["규제"]["지역가정"] is True
    assert local["가정"]["지역코드"] is None


def test_price_scenario_rejects_name_only_collision(client, monkeypatch):
    source = SimpleNamespace(codes={"중구": "1114000000"},
                             regions=["중구"], identity_verified=True)
    monkeypatch.setattr(md, "kb", lambda: source)
    client.post("/api/buying-power/confirm", json={"capital": 50000, "income": 9000})
    assert client.get("/api/buying-power/scenario", params={"price": 90000, "region": "중구"}).status_code == 422
    verified = client.get("/api/buying-power/scenario", params={
        "price": 90000, "region": "중구", "region_code": "kb:1114000000"}).json()
    assert verified["지역식별"] == "verified"
    assert verified["지역코드"] == "kb:1114000000"


def test_explicit_finance_code_does_not_fall_back_during_kb_outage(client, monkeypatch):
    monkeypatch.setattr(md, "kb", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    payload = {"capital": 50000, "income": 9000, "region": "중구",
               "region_code": "kb:1114000000"}
    assert client.get("/api/buying-power", params=payload).status_code == 503
    assert client.post("/api/buying-power/confirm", json=payload).status_code == 503
    assert "매수력" not in (client.get("/api/auth/me").json()["profile"] or {})


def test_regulation_api_lists_current_designations(client):
    d = client.get("/api/regulation").json()
    assert d["asof"].startswith("2026-07-01")
    assert len(d["map"]) == 40                       # 서울 25 + 경기 15
    assert "투기과열지구" in d["map"]["강남구"]
    assert "화성시 동탄구" in d["map"]
    assert "화성시" in d["notes"]


def test_confirm_rejects_zero_capital(client):
    r = client.post("/api/buying-power/confirm", json={"capital": 0})
    assert r.status_code == 400


def test_shortlist_needs_budget(client):
    d = client.get("/api/shortlist").json()
    assert d["ready"] is False
    assert d["reason"] == "no_budget"


def test_shortlist_uses_confirmed_budget(client, monkeypatch):
    client.post("/api/buying-power/confirm", json={"capital": 50000, "income": 8000})
    monkeypatch.setattr(app_api, "_display_signal_map", lambda: {"노원구": "BUY"})
    monkeypatch.setattr(app_api, "_region_grades",
                        lambda r: [{"단지": "상계주공", "평단가": 2000, "급지": 3, "상위": 45}])
    monkeypatch.setattr(sl, "_locality_map", lambda: {"노원구": {"region": "노원구", "저평가도": 10}})
    monkeypatch.setattr(sl, "_region_commute", lambda region, work: None)
    d = client.get("/api/shortlist").json()
    assert d["ready"] is True
    assert d["확정예산"] is True
    assert d["candidates"][0]["단지"] == "상계주공"
    assert d["candidates"][0]["자금"]["월상환"] > 0
