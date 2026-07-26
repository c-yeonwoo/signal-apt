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


def test_stale_data_warning_is_wired():
    """0 이 '평온'인지 '수집 중단'인지 화면이 구분해야 한다."""
    html = INDEX.read_text(encoding="utf-8")
    assert "d.stale_days>10" in html
    assert "일째" in html
