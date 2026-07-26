"""홈 '이번 주' — 주간 diff · 액션플랜 · 뉴스 창.

특히 지키려는 것: **0 에 이유가 붙는가**. "변화 없음"과 "비교 대상 없음"과
"계산 실패"가 화면에서 같아 보이면 고장을 몇 주씩 못 본다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from realty_signal import briefing, db, weekly
from realty_signal.brain import snapshots
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.signals import history


def _kb(rows: list[tuple]) -> KBWeekly:
    """(date, region, metric, value) 튜플로 최소 KBWeekly 구성."""
    df = pd.DataFrame(rows, columns=["date", "region", "metric", "value"])
    df["date"] = pd.to_datetime(df["date"])
    return KBWeekly(long=df)


# ---------------------------------------------------------------- weekly diff

def test_single_week_is_blocked_with_a_reason():
    """주가 하나뿐이면 '변화 없음'이 아니라 '비교할 지난주가 없음'이어야 한다."""
    kb = _kb([("2026-07-20", "강남구", "jeonse_supply", 120.0)])
    out = weekly.compute(kb, supply=None)
    assert out["ready"] is False
    assert out["blocked_reason"] and "지난주" in out["blocked_reason"]
    assert out["signals"] == []


def test_weekly_diff_uses_last_week_slice_not_a_stored_snapshot(monkeypatch):
    """저장된 스냅샷이 아니라 KB 시계열을 잘라 다시 평가한다 — 래치·순서에 의존하지 않는다."""
    calls = []

    def fake_eval(kb, cfg=None, supply=None, **kw):
        last = str(kb.last_date.date())
        calls.append(last)
        sig = "STRONG_BUY" if last == "2026-07-20" else "BUY"
        return pd.DataFrame([{"region": "강남구", "signal": sig, "근거": "t",
                              "전세수급": 100.0, "매수우위지수": 50.0,
                              "매수세우위": 10.0, "매매모멘텀": 0.1, "수급출처": None}])

    monkeypatch.setattr(weekly, "evaluate", fake_eval)
    kb = _kb([("2026-07-13", "강남구", "jeonse_supply", 110.0),
              ("2026-07-20", "강남구", "jeonse_supply", 120.0)])
    out = weekly.compute(kb, supply=None)

    assert calls == ["2026-07-20", "2026-07-13"]   # 이번 주 · 지난주 두 번 평가
    assert out["ready"] and out["prev"] == "2026-07-13"
    assert [s["region"] for s in out["signals"]] == ["강남구"]
    s = out["signals"][0]
    assert (s["from"], s["to"], s["up"]) == ("BUY", "STRONG_BUY", True)
    assert s["from_ko"] == "매수" and s["to_ko"] == "적극매수"
    assert out["totals"] == {"regions": 1, "up": 1, "down": 0, "movers": 0}


def test_inherited_metric_movers_collapse_to_one_row(monkeypatch):
    """광역 공통 지표는 시군구마다 늘어놓지 않는다 — 같은 숫자 15줄은 목록이 아니라 벽이다."""
    def fake_eval(kb, cfg=None, supply=None, **kw):
        cur = str(kb.last_date.date()) == "2026-07-20"
        val = 80.0 if cur else 95.0
        rows = [{"region": r, "signal": "BUY", "근거": "t", "전세수급": 100.0,
                 "매수우위지수": val, "매수세우위": 10.0, "매매모멘텀": 0.1,
                 "수급출처": "강북14개구"} for r in ("노원구", "도봉구", "광진구")]
        return pd.DataFrame(rows)

    monkeypatch.setattr(weekly, "evaluate", fake_eval)
    kb = _kb([("2026-07-13", "노원구", "m", 1.0), ("2026-07-20", "노원구", "m", 2.0)])
    movers = weekly.compute(kb, supply=None)["movers"]

    assert len(movers) == 1
    assert movers[0]["region"] == "강북14개구"
    assert movers[0]["shared"] is True and movers[0]["regions"] == 3
    assert movers[0]["delta"] == -15.0


def test_for_user_separates_my_regions(monkeypatch):
    monkeypatch.setattr(weekly, "latest", lambda kb=None, supply=None: {
        "as_of": "2026-07-20", "prev": "2026-07-13", "ready": True,
        "signals": [{"region": "강남구", "up": True}, {"region": "제주", "up": False}],
        "movers": [{"region": "강남구"}, {"region": "충북"}],
    })
    out = weekly.for_user({"강남구"})
    assert [s["region"] for s in out["mine"]] == ["강남구"]
    assert [s["region"] for s in out["rest"]] == ["제주"]
    assert [m["region"] for m in out["my_movers"]] == ["강남구"]


# ------------------------------------------------------------- 변화 로그 구멍

@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    monkeypatch.setattr(history, "SNAPSHOT_FILE", tmp_path / "snapshot.json")
    db._migrated[0] = False
    return tmp_path


def test_advance_logs_changes_so_cli_and_server_cannot_diverge(isolated_db):
    """스냅샷을 당기는 모든 경로가 로그를 남긴다.

    CLI 가 스냅샷만 앞으로 당기고 서버만 로그를 쓰던 시절, 토요일 CLI 가 먼저 돌면
    서버는 prev == cur 을 보고 '변화 없음'을 남겼다. 실제로 두 주가 통째로 비었다.
    """
    assert snapshots.advance({"강남구": "BUY"}, "2026-07-13") == []      # 최초는 기준점만
    assert db.kv_get(snapshots.CHANGE_LOG_KEY) is None

    changes = snapshots.advance({"강남구": "STRONG_BUY"}, "2026-07-20")
    assert len(changes) == 1 and changes[0]["to"] == "STRONG_BUY"
    log = db.kv_get(snapshots.CHANGE_LOG_KEY)
    assert log[0] == {"region": "강남구", "from": "BUY", "to": "STRONG_BUY", "date": "2026-07-20"}


def test_advance_is_idempotent_per_week(isolated_db):
    """같은 주를 두 번 처리해도 로그가 부풀지 않는다(CLI·서버가 같은 날 둘 다 돌 수 있다)."""
    snapshots.advance({"강남구": "BUY"}, "2026-07-13")
    snapshots.advance({"강남구": "STRONG_BUY"}, "2026-07-20")
    snapshots.log_changes([{"region": "강남구", "from": "BUY", "to": "STRONG_BUY"}], "2026-07-20")
    assert len(db.kv_get(snapshots.CHANGE_LOG_KEY)) == 1


# ----------------------------------------------------------------- 액션플랜

_NO_DIFF = {"new": [], "dropped": [], "moved": []}


def test_auction_deadline_outranks_everything():
    """기일은 미룰 수 없다 — 예산 미입력보다도 먼저 온다."""
    acts = briefing.actions(
        {"new": [{"단지": "새후보", "region": "노원구"}], "dropped": [], "moved": []},
        [{"region": "노원구", "from": "WATCH", "to": "BUY", "up": True}],
        [{"지역": "금천구"}], [], None,
        [{"kind": "bid", "D": 0, "단지": "상계주공7", "region": "노원구", "날짜": "2026-07-27"}],
        profile={}, budget=0)
    assert acts[0]["key"] == "auction_bid" and acts[0]["urgent"] is True
    assert acts[1]["key"] == "no_budget"


def test_no_budget_does_not_also_nag_about_favorites():
    """★가 있는데 예산이 없어 후보가 빈 경우, ★를 또 달라고 하지 않는다."""
    acts = briefing.actions(_NO_DIFF, [], [], [], profile={"_favs": ["노원구"]}, budget=0)
    keys = [a["key"] for a in acts]
    assert "no_budget" in keys and "no_favorite" not in keys


def test_confirm_prompt_only_when_unconfirmed():
    base = dict(profile={"_favs": ["노원구"], "직장": "강남"}, budget=50000)
    assert "confirm_power" in [a["key"] for a in briefing.actions(
        _NO_DIFF, [], [], [], confirmed=False, **base)]
    assert "confirm_power" not in [a["key"] for a in briefing.actions(
        _NO_DIFF, [], [], [], confirmed=True, **base)]


def test_todo_line_is_the_first_action():
    """텔레그램 한 줄과 홈 1번이 갈라지면 앱이 서로 다른 말을 한다."""
    args = (_NO_DIFF, [{"region": "노원구", "from": "WATCH", "to": "BUY", "up": True}], [], [])
    kwargs = dict(profile={"_favs": ["노원구"], "직장": "강남"}, budget=50000)
    assert briefing._todo(*args, None, None, kwargs["profile"], kwargs["budget"]) \
        == briefing.actions(*args, **kwargs)[0]["title"]
