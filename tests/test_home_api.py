"""홈 API 3종 + 뉴스 탭 제거 회귀.

숫자 로직은 test_home_weekly.py 가 본다. 여기서는 **배선**만 본다 —
로그인 경계, 실패가 '변화 없음'으로 둔갑하지 않는지, 화면에서 뉴스 탭이 실제로 사라졌는지.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from realty_signal import api as app_api
from realty_signal import auth, db, weekly
from realty_signal.routes import auth as auth_routes
from realty_signal.routes import home as home_routes
from realty_signal.services import budget_watch as bw
from realty_signal.services import complex_watch as cw
from realty_signal.services import market_data as md

INDEX = Path(__file__).resolve().parents[1] / "src/realty_signal/web/index.html"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    for k in ("INVITE_CODES", "STUDENT_ALLOWLIST", "RAILWAY_ENVIRONMENT", "APP_ENV"):
        monkeypatch.delenv(k, raising=False)
    token, err = auth.signup("home@example.com", "secret1", accept_tos=True)
    assert err is None
    c = TestClient(app_api.app)
    c.cookies.set(auth.COOKIE, token)
    return c


_FAKE_WEEK = {"as_of": "2026-07-20", "prev": "2026-07-13", "ready": True,
              "stale_days": 3, "blocked_reason": None,
              "signals": [{"region": "강남구", "up": True}],
              "movers": [], "totals": {"regions": 1, "up": 1, "down": 0, "movers": 0}}


def test_weekly_change_splits_my_regions(client, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(weekly, "latest", lambda kb=None, supply=None: dict(_FAKE_WEEK))
    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(
        codes={"강남구": "1168000000"}, regions=["강남구"], identity_verified=True))
    client.post("/api/favorites", json={"kind": "region", "key": "강남구"})
    d = client.get("/api/weekly-change").json()
    assert d["ready"] is True and d["prev"] == "2026-07-13"
    assert [s["region"] for s in d["mine"]] == ["강남구"]
    assert d["rest"] == []


def test_weekly_change_failure_is_not_silence(client, monkeypatch):
    """계산이 터졌는데 '이번 주 변화 없음'으로 보이면 고장을 몇 주씩 못 본다."""
    def boom(*a, **kw):
        raise RuntimeError("캐시 깨짐")

    monkeypatch.setattr(weekly, "for_user", boom)
    d = client.get("/api/weekly-change").json()
    assert d["ready"] is False
    assert "캐시 깨짐" in d["blocked_reason"]


def test_weekly_visit_is_consumed_only_by_seen_ack(client, monkeypatch):
    """GET 성공이나 렌더 실패만으로 복귀 기준점을 잃으면 안 된다."""
    from types import SimpleNamespace

    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(last_date=__import__("pandas").Timestamp("2026-07-20")))
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    assert db.kv_get("last_visit:" + str(uid)) is None

    r = client.post("/api/weekly-change/seen", json={"as_of": "2026-07-20"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert db.kv_get("last_visit:" + str(uid))["as_of"] == "2026-07-20"


def test_budget_watch_is_consumed_only_by_seen_ack(client, monkeypatch):
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    confirmed = client.post("/api/buying-power/confirm", json={"capital": 100_000, "income": 0})
    assert confirmed.status_code == 200
    rows = [{"key": "급매:1", "총액": 80_000, "유형": "급매", "단지명": "테스트", "지역": "노원구"}]
    monkeypatch.setattr(app_api, "_build_listings", lambda kinds, **_kw: rows)

    d = client.get("/api/budget-watch").json()
    assert d["reason"] == "first_run"
    assert db.kv_get(bw.KV_PREFIX + str(uid)) is None

    assert client.post("/api/budget-watch/seen").json()["ok"] is True
    assert db.kv_get(bw.KV_PREFIX + str(uid))["items"]


def test_budget_watch_rejects_stale_saved_max_without_advancing_seen(client, monkeypatch):
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.profile_set(uid, {"매수력": {"최대매수가": 90_000}})
    monkeypatch.setattr(app_api, "_build_listings", lambda kinds, **_kw: (
        (_ for _ in ()).throw(AssertionError("stale budget must stop before listing fetch"))))
    result = client.get("/api/budget-watch").json()
    assert result["ready"] is False and result["reason"] == "reconfirm_required"
    assert client.post("/api/budget-watch/seen").json() == {
        "ok": False, "reason": "reconfirm_required"}
    assert db.kv_get(bw.KV_PREFIX + str(uid)) is None

    client.post("/api/buying-power/confirm", json={"capital": 100_000, "income": 0})
    profile = db.profile_get(uid)
    profile["가용자본"] = 120_000
    db.profile_set(uid, profile)
    changed = client.get("/api/budget-watch").json()
    assert changed["reason"] == "reconfirm_required"


def test_complex_watch_ignores_stale_budget_but_keeps_trade_updates(client, monkeypatch):
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.profile_set(uid, {"매수력": {"최대매수가": 90_000}})
    monkeypatch.setattr(cw, "favorites_of", lambda _uid: [("노원구", "테스트아파트")])
    monkeypatch.setattr(cw, "cache_loader", lambda: lambda *_args: ({}, None))
    budgets = []
    monkeypatch.setattr(cw, "compute", lambda _uid, _favs, _loader, *, budget: (
        budgets.append(budget) or {"ready": True, "_snaps": {}}))
    assert client.get("/api/complex-watch").json()["ready"] is True
    assert budgets == [None]


def test_complex_watch_is_consumed_only_by_seen_ack(client, monkeypatch):
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    monkeypatch.setattr(auth_routes.md, "code_of", lambda region: "11680")
    client.post("/api/favorites", json={"kind": "complex", "key": "강남구|테스트아파트"})
    payload = {"매매추이": [{"ym": "2026-07", "건수": 1}], "평형별": [], "최근평단가": 4000}
    monkeypatch.setattr(cw, "cache_loader", lambda: lambda region, name: (payload, 1_700_000_000))

    assert client.get("/api/complex-watch").json()["ready"] is True
    assert db.kv_get(cw.KV_PREFIX + str(uid)) is None

    assert client.post("/api/complex-watch/seen").json()["ok"] is True
    assert db.kv_get(cw.KV_PREFIX + str(uid))["items"]


def test_complex_favorite_queues_first_real_trade_warm(client, monkeypatch):
    """★ 등록 직후 워밍을 예약해야 다음 홈 방문에서 '아직 수집 안 됨'이 줄어든다."""
    called = []
    monkeypatch.setattr(auth_routes.md, "code_of", lambda region: "11680")
    monkeypatch.setattr(auth_routes, "_warm_complex_after_favorite",
                        lambda region, name: called.append((region, name)))

    r = client.post("/api/favorites", json={"kind": "complex", "key": "강남구|테스트아파트"})
    assert r.json() == {"ok": True, "warming": "queued"}
    assert called == [("강남구", "테스트아파트")]


def test_complex_favorite_rejects_sido_without_creating_false_watch(client, monkeypatch):
    """시·도 단위는 단지 API가 조회할 수 없으므로 워밍 약속을 하면 안 된다."""
    monkeypatch.setattr(auth_routes.md, "code_of", lambda region: "11000")

    r = client.post("/api/favorites", json={"kind": "complex", "key": "서울|상계주공9단지"})

    assert r.status_code == 422
    assert r.json()["error"] == "untrackable_complex"
    assert "시군구" in r.json()["message"]
    assert client.get("/api/favorites").json()["favorites"] == []


def test_complex_favorite_rejects_name_only_jung_gu(client, monkeypatch):
    monkeypatch.setattr(auth_routes.md, "code_of", lambda region: pytest.fail(
        "모호한 이름은 코드 조회 전에 거부해야 합니다"))
    response = client.post("/api/favorites", json={
        "kind": "complex", "key": "중구|옛 관심단지"})
    assert response.status_code == 422
    assert response.json()["error"] == "untrackable_complex"
    assert "서울·개편 전 인천" in response.json()["message"]
    assert client.get("/api/favorites").json()["favorites"] == []

    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.fav_add(uid, "complex", "중구|옛 관심단지", "옛 관심단지")
    old = client.get("/api/favorites").json()["favorites"][0]
    assert old["key"] == "중구|옛 관심단지"
    assert old["complex_identity"]["status"] == "needs_reselection"
    watched = client.get("/api/complex-watch").json()
    assert watched["unavailable"][0]["key"] == old["key"]
    assert watched["moved_total"] == 0


def test_complex_favorite_verified_code_keeps_old_jung_gu_separate(client, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(
        codes={"중구": "1114000000"}, regions=["중구"], identity_verified=True))
    warmed = []
    monkeypatch.setattr(auth_routes, "_warm_complex_after_favorite",
                        lambda region, name: warmed.append((region, name)))
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.fav_add(uid, "complex", "중구|옛 관심단지", "옛 관심단지")

    selected = client.post("/api/favorites", json={
        "kind": "complex", "key": "kb:1114000000|옛 관심단지"})
    assert selected.status_code == 200
    assert selected.json()["key"] == "kb:1114000000|옛 관심단지"
    assert warmed == [("kb:1114000000", "옛 관심단지")]
    records = {item["key"]: item for item in client.get("/api/favorites").json()["favorites"]}
    assert records["중구|옛 관심단지"]["complex_identity"]["status"] == "needs_reselection"
    assert records["kb:1114000000|옛 관심단지"]["complex_identity"]["label"] == "서울 · 중구"
    assert db.complex_favorite_region("kb:1114000000")["code"] == "1114000000"

    bad = client.post("/api/favorites", json={
        "kind": "complex", "key": "kb:2811000000|옛 관심단지"})
    assert bad.status_code == 422


def test_pre_reform_region_favorite_is_preserved_but_needs_reselection(client, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(auth_routes.md, "kb", lambda: SimpleNamespace(
        codes={"서구": "2826000000", "강남구": "1168000000", "제물포구": "2812500000"},
        regions=["서구", "강남구"], identity_verified=True))
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.fav_add(uid, "region", "서구", "서구")  # 기존 저장본은 자동 삭제·이전하지 않는다.
    response = client.post("/api/favorites", json={"kind": "region", "key": "서구"})
    assert response.status_code == 422 and response.json()["error"] == "retired_region"
    retired_code = client.post("/api/favorites", json={"kind": "region", "key": "kb:2826000000"})
    assert retired_code.status_code == 422 and retired_code.json()["error"] == "retired_region"
    created = client.post("/api/favorites", json={"kind": "region", "key": "강남구"}).json()
    assert created == {"ok": True, "key": "kb:1168000000"}
    db.fav_add(uid, "region", "제물포구", "제물포구")
    favorites = {f["key"]: f for f in client.get("/api/favorites").json()["favorites"]}
    assert favorites["서구"]["region_identity"]["status"] == "needs_reselection"
    assert favorites["kb:1168000000"]["region_identity"]["status"] == "ready"
    assert favorites["제물포구"]["region_identity"]["status"] == "no_current_series"


def test_region_favorite_identity_failure_does_not_claim_ready(client, monkeypatch):
    monkeypatch.setattr(auth_routes.md, "kb", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.fav_add(uid, "region", "강남구", "강남구")
    favorite = client.get("/api/favorites").json()["favorites"][0]
    assert favorite["region_identity"]["status"] == "unverified"


def test_legacy_jung_gu_favorite_is_archived_not_personalized_as_seoul(client, monkeypatch):
    """A name-only pre-reform Incheon favorite cannot become Seoul by accident."""
    from types import SimpleNamespace

    monkeypatch.setattr(auth_routes.md, "kb", lambda: SimpleNamespace(
        codes={"중구": "1114000000", "강남구": "1168000000"},
        regions=["중구", "강남구"], identity_verified=True))
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.fav_add(uid, "region", "중구", "중구")
    assert client.post("/api/favorites", json={"kind": "region", "key": "중구"}).status_code == 422
    favorites = client.get("/api/favorites").json()["favorites"]
    assert favorites[0]["key"] == "중구"
    assert favorites[0]["region_identity"]["status"] == "needs_reselection"
    assert db.actionable_region_favs(uid) == []
    assert "중구" not in db.all_fav_regions()
    assert db.users_with_region_favs() == []

    assert client.post("/api/favorites", json={"kind": "region", "key": "강남구"}).json()["ok"]
    assert db.actionable_region_favs(uid) == ["강남구"]
    assert db.users_with_region_favs()[0]["regions"] == ["강남구"]


def test_verified_code_favorite_reselects_jung_gu_without_rewriting_legacy(client, monkeypatch):
    from types import SimpleNamespace

    source = SimpleNamespace(
        codes={"중구": "1114000000", "강남구": "1168000000"},
        regions=["중구", "강남구"], identity_verified=True)
    monkeypatch.setattr(md, "kb", lambda: source)
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    db.fav_add(uid, "region", "중구", "중구")

    response = client.post("/api/favorites", json={
        "kind": "region", "key": "kb:1114000000", "label": "공격자가 고른 이름"})
    assert response.status_code == 200
    favorites = {f["key"]: f for f in client.get("/api/favorites").json()["favorites"]}
    assert favorites["중구"]["region_identity"]["status"] == "needs_reselection"
    assert favorites["kb:1114000000"]["region_identity"] == {
        "status": "ready", "name": "중구", "region_id": "kb:1114000000",
        "label": "서울 · 중구", "message": ""}
    assert favorites["kb:1114000000"]["label"] == "서울 · 중구"
    assert db.actionable_region_favs(uid) == ["중구"]
    assert db.all_fav_regions() == ["중구"]
    assert db.users_with_region_favs()[0]["regions"] == ["중구"]

    source.identity_verified = False
    assert db.actionable_region_favs(uid) == []
    assert db.users_with_region_favs() == []
    assert {f["key"]: f for f in client.get("/api/favorites").json()["favorites"]}[
        "kb:1114000000"]["region_identity"]["status"] == "unverified"
    source.identity_verified = True

    assert client.delete("/api/favorites", params={"kind": "region", "key": "kb:1114000000"}).json()["ok"]
    assert db.actionable_region_favs(uid) == []
    assert [f["key"] for f in client.get("/api/favorites").json()["favorites"]] == ["중구"]


def test_unverified_code_favorite_is_rejected_not_saved(client, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(
        codes={"중구": "1114000000"}, regions=["중구"], identity_verified=False))
    response = client.post("/api/favorites", json={"kind": "region", "key": "kb:1114000000"})
    assert response.status_code == 422
    assert response.json()["error"] == "unverified_region"
    assert client.get("/api/favorites").json()["favorites"] == []


def test_region_favorite_rejects_code_shared_by_two_current_names(client, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(
        codes={"강남구": "1168000000", "다른 구": "1168000000"},
        regions=["강남구", "다른 구"], identity_verified=True))
    for key in ("강남구", "kb:1168000000"):
        response = client.post("/api/favorites", json={"kind": "region", "key": key})
        assert response.status_code == 422
        assert response.json()["error"] == "unverified_region"
    assert client.get("/api/favorites").json()["favorites"] == []


def test_favorite_endpoints_require_a_user_account(client):
    client.cookies.clear()
    assert client.get("/api/favorites").status_code == 401
    assert client.post("/api/favorites", json={"kind": "region", "key": "강남구"}).status_code == 401
    assert client.delete("/api/favorites", params={"kind": "region", "key": "강남구"}).status_code == 401
    assert db.fav_list(None) == []


def test_action_plan_requires_login(client):
    client.cookies.clear()
    assert client.get("/api/action-plan").status_code == 401


def test_action_plan_returns_structured_steps(client, monkeypatch):
    monkeypatch.setattr(weekly, "latest", lambda kb=None, supply=None: dict(_FAKE_WEEK))
    d = client.get("/api/action-plan").json()
    assert d["ok"] is True and d["actions"]
    for a in d["actions"]:                     # 홈이 버튼을 그리려면 넷 다 있어야 한다
        assert {"key", "title", "why", "tab", "cta"} <= set(a)


def test_weekly_issues_window_is_capped_and_explains_empty(client, monkeypatch):
    """홈은 아카이브가 아니다 — 창을 좁게 고정하고, 비면 이유를 말한다."""
    seen = {}

    def fake_since(topic, days, limit):
        seen["days"] = days
        return []

    monkeypatch.setattr(db, "news_since", fake_since)
    monkeypatch.setattr(app_api, "news", lambda topic=None: {"items": []})
    d = client.get("/api/weekly-issues").json()
    assert seen["days"] == home_routes.NEWS_WINDOW_DAYS == 7
    assert d["count"] == 0 and "7일" in d["blocked_reason"]

    client.get("/api/weekly-issues?days=999")
    assert seen["days"] == 30            # 상한을 넘겨도 아카이브가 되지는 않는다


# ------------------------------------------------------------------ 뉴스 탭 제거

def test_news_tab_is_gone_from_the_shell():
    html = INDEX.read_text(encoding="utf-8")
    assert "switchTab('news')" not in html
    assert 'id="view-news"' not in html
    assert "loadNews" not in html
    assert "'report','news'" not in html          # _ALLVIEWS 잔재


def test_old_news_link_lands_on_home():
    """없앤 탭의 옛 북마크가 아무 데도 못 가면 안 된다."""
    html = INDEX.read_text(encoding="utf-8")
    assert "_GONE={news:'dashboard', report:'dashboard', undervalued:'signal'}" in html
    assert "if(_GONE[h]){ switchTab(_GONE[h], true); return true; }" in html


def test_home_renders_the_three_new_cards():
    html = INDEX.read_text(encoding="utf-8")
    for wrap in ("dashWeeklyWrap", "dashPlanWrap", "dashIssuesWrap"):
        assert f'id="{wrap}"' in html and f"getElementById('{wrap}')" in html
    assert "/api/weekly-change" in html
    assert "/api/action-plan" in html
    assert "/api/weekly-issues" in html
    # 주간 카드가 옛 ★변동 카드를 대체했다 — 같은 걸 두 번 그리지 않는다
    assert "dashChangesWrap" not in html


def test_change_cards_ack_only_after_entering_the_viewport():
    html = INDEX.read_text(encoding="utf-8")
    assert "IntersectionObserver" in html
    for endpoint in ("/api/weekly-change/seen", "/api/budget-watch/seen", "/api/complex-watch/seen"):
        assert endpoint in html


def test_complex_favorite_tells_the_buyer_that_warming_started():
    html = INDEX.read_text(encoding="utf-8")
    assert "실거래 변화를 준비하는 중입니다" in html
    assert "_favs.delete(id)" in html
    assert "out.message" in html


def test_stale_data_warning_is_wired():
    """관측 지연을 최신 시장 변화 없음으로 해석하지 않아야 한다."""
    html = INDEX.read_text(encoding="utf-8")
    assert "d.stale_days > 8" in html or "d.stale_days>8" in html
    assert "KB 관측 기준일이" in html


def test_stale_banner_separates_observation_from_collection():
    """오래된 KB 관측과 마지막 수집 시각을 분리하고 원천·수집 실패를 단정하지 않는다."""
    html = INDEX.read_text(encoding="utf-8")
    assert "KB 관측 기준일이" in html
    assert "마지막 성공 수집이" in html
    assert "관측 지연은 시장 변화가 없다는 뜻이 아닙니다" in html
    visible = "\n".join(l for l in html.splitlines() if not l.lstrip().startswith("//"))
    assert "데이터를 ${days}일째 받아오지 못했습니다" not in visible
    assert "kb_fetch" in html and "consecutive" in html


def test_weekly_change_carries_fetch_health():
    """배너가 원인을 말하려면 API 가 수집 건강 상태를 함께 줘야 한다."""
    import inspect

    from realty_signal.routes import home as home_routes

    src = inspect.getsource(home_routes.weekly_change)
    assert "kb_fetch" in src and "kb_fetch_health" in src
