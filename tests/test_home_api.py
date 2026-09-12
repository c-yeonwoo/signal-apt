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
    monkeypatch.setattr(weekly, "latest", lambda kb=None, supply=None: dict(_FAKE_WEEK))
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
    db.profile_set(uid, {"매수력": {"최대매수가": 90_000}})
    rows = [{"key": "급매:1", "총액": 80_000, "유형": "급매", "단지명": "테스트", "지역": "노원구"}]
    monkeypatch.setattr(app_api, "_build_listings", lambda kinds: rows)

    d = client.get("/api/budget-watch").json()
    assert d["reason"] == "first_run"
    assert db.kv_get(bw.KV_PREFIX + str(uid)) is None

    assert client.post("/api/budget-watch/seen").json()["ok"] is True
    assert db.kv_get(bw.KV_PREFIX + str(uid))["items"]


def test_complex_watch_is_consumed_only_by_seen_ack(client, monkeypatch):
    uid = auth.current_user(client.cookies.get(auth.COOKIE))["id"]
    client.post("/api/favorites", json={"kind": "complex", "key": "강남구|테스트아파트"})
    payload = {"매매추이": [{"ym": "2026-07", "건수": 1}], "평형별": [], "최근평단가": 4000}
    monkeypatch.setattr(cw, "cache_loader", lambda: lambda region, name: (payload, 1_700_000_000))

    assert client.get("/api/complex-watch").json()["ready"] is True
    assert db.kv_get(cw.KV_PREFIX + str(uid)) is None

    assert client.post("/api/complex-watch/seen").json()["ok"] is True
    assert db.kv_get(cw.KV_PREFIX + str(uid))["items"]


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
    assert "_GONE={news:'dashboard'}" in html
    assert "if(_GONE[h]){ switchTab(_GONE[h]); return true; }" in html


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


def test_stale_data_warning_is_wired():
    """0 이 '평온'인지 '수집 중단'인지 화면이 구분해야 한다."""
    html = INDEX.read_text(encoding="utf-8")
    assert "d.stale_days > 10" in html or "d.stale_days>10" in html
    assert "일째" in html


def test_stale_banner_blames_collection_not_the_market():
    """배너는 '시장이 조용하다' 가 아니라 '우리가 못 받아왔다' 라고 말해야 한다.

    옛 문구 "KB 데이터가 N일째 그대로입니다" 는 읽으면 '시장이 안 변했다' 로 들렸고,
    실제로 그 오해가 "분석이 너무 부동하다" 는 인상을 만들었다(2026-09-06 조사).
    같은 기간 KB 는 정상 발표를 계속했고 멈춘 건 우리 수집이었다.
    """
    html = INDEX.read_text(encoding="utf-8")
    assert "받아오지 못했습니다" in html, "수집 주체를 밝히는 문구가 없다"
    assert "시장이 조용한 것과는 다른 상태" in html, "시장 정적과 수집 중단을 구분하지 않는다"
    # 주석이 아니라 **화면에 나가는 문자열**만 본다 (주석에는 옛 문구가 기록으로 남아 있다)
    visible = "\n".join(l for l in html.splitlines() if not l.lstrip().startswith("//"))
    assert "그대로입니다" not in visible, "옛 오해 유발 문구가 화면 문자열로 되살아났다"
    # 원인(연속 실패·마지막 성공)을 배너가 직접 말할 수 있어야 한다
    assert "kb_fetch" in html and "consecutive" in html


def test_weekly_change_carries_fetch_health():
    """배너가 원인을 말하려면 API 가 수집 건강 상태를 함께 줘야 한다."""
    import inspect

    from realty_signal.routes import home as home_routes

    src = inspect.getsource(home_routes.weekly_change)
    assert "kb_fetch" in src and "kb_fetch_health" in src
