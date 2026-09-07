"""복귀 브리핑 — 자리를 비운 동안 놓친 것.

홈의 '이번 주 변화' 는 직전 1주만 비교하므로, 5주 비운 사용자는 그 사이 변화를
구조적으로 잃는다(2026-09-06 조사: 강남구가 STRONG_BUY→BUY→WATCH 로 두 단계
내려갔는데 8/10 의 첫 강등은 화면에 뜬 적이 없다).
"""

from __future__ import annotations

import json
import pathlib
import re

from realty_signal import db
from realty_signal.services import comeback as cb

DATES = ["2026-07-20", "2026-07-27", "2026-08-03", "2026-08-10",
         "2026-08-17", "2026-08-24", "2026-08-31"]
LOG = [
    {"region": "강남구", "from": "STRONG_BUY", "to": "BUY", "date": "2026-08-10"},
    {"region": "강남구", "from": "BUY", "to": "WATCH", "date": "2026-08-31"},
    {"region": "서초구", "from": "STRONG_BUY", "to": "BUY", "date": "2026-08-31"},
    {"region": "대전", "from": "WATCH", "to": "BUY", "date": "2026-08-31"},
    {"region": "오래된곳", "from": "BUY", "to": "WATCH", "date": "2026-07-06"},   # 구간 밖
]


def _setup(tmp_path, monkeypatch, last_visit="2026-07-27"):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False
    db.kv_set("signal_changes", LOG)
    if last_visit:
        db.kv_set(cb.KV_PREFIX + "1", {"as_of": last_visit})


def test_multi_step_path_is_restored(tmp_path, monkeypatch):
    """두 단계 강등의 **경로**가 날짜까지 복원돼야 한다 — 순변화만 보여주면 반쪽이다."""
    _setup(tmp_path, monkeypatch)
    out = cb.compute(1, {"강남구"}, "2026-08-31", DATES)
    assert out["ready"] is True and out["weeks"] == 5
    mine = out["mine"]
    assert len(mine) == 1 and mine[0]["region"] == "강남구"
    v = mine[0]
    assert (v["from"], v["to"]) == ("STRONG_BUY", "WATCH")
    assert v["steps"] == 2
    assert [s["date"] for s in v["path"]] == ["2026-08-10", "2026-08-31"]


def test_out_of_window_changes_are_excluded(tmp_path, monkeypatch):
    """마지막 방문 이전 변화는 이미 봤다 — 다시 보여주지 않는다."""
    _setup(tmp_path, monkeypatch)
    out = cb.compute(1, set(), "2026-08-31", DATES)
    assert "오래된곳" not in {v["region"] for v in out["rest"]}


def test_recent_visit_does_not_duplicate_weekly_card(tmp_path, monkeypatch):
    """1주 차이면 '이번 주 변화' 가 이미 같은 말을 한다 — 중복 금지."""
    _setup(tmp_path, monkeypatch, last_visit="2026-08-24")
    out = cb.compute(1, set(), "2026-08-31", DATES)
    assert out["ready"] is False and out["reason"] == "recent_visit"


def test_first_visit_has_a_reason_not_silence(tmp_path, monkeypatch):
    """0 을 이유 없이 비우지 않는다 — 첫 방문·최근 방문·조용함은 다른 상태다."""
    _setup(tmp_path, monkeypatch, last_visit=None)
    assert cb.compute(1, set(), "2026-08-31", DATES)["reason"] == "first_visit"


def test_quiet_period_is_still_reported(tmp_path, monkeypatch):
    """변화가 없어도 '조용한 5주였습니다' 라고 말한다 — 빈 화면은 고장처럼 보인다."""
    _setup(tmp_path, monkeypatch)
    db.kv_set("signal_changes", [])
    out = cb.compute(1, set(), "2026-08-31", DATES)
    assert out["ready"] is True and out["quiet"] is True


def test_round_trip_change_is_not_reported(tmp_path, monkeypatch):
    """갔다가 돌아왔으면 순변화가 없다 — 알릴 것도 없다."""
    _setup(tmp_path, monkeypatch)
    db.kv_set("signal_changes", [
        {"region": "왕복구", "from": "BUY", "to": "WATCH", "date": "2026-08-10"},
        {"region": "왕복구", "from": "WATCH", "to": "BUY", "date": "2026-08-31"},
    ])
    out = cb.compute(1, set(), "2026-08-31", DATES)
    assert out["totals"]["regions"] == 0


def test_mark_seen_consumes_the_window_once(tmp_path, monkeypatch):
    """기준점은 **보여준 뒤에** 옮긴다. 두 번 보여주지 않는다."""
    _setup(tmp_path, monkeypatch)
    assert cb.compute(1, set(), "2026-08-31", DATES)["ready"] is True
    cb.mark_seen(1, "2026-08-31")
    again = cb.compute(1, set(), "2026-08-31", DATES)
    assert again["ready"] is False and again["reason"] == "no_new_week"


def test_uses_its_own_key_not_the_telegram_snapshot():
    """텔레그램 브리핑과 키를 공유하면 발송될 때마다 복귀 브리핑이 사라진다."""
    assert cb.KV_PREFIX != "briefing_snap:"
    assert "last_visit" in cb.KV_PREFIX


# ── R2: 매수 대기자 관점 라벨 ──────────────────────────────────

def test_buyer_note_direction():
    assert cb.buyer_note("STRONG_BUY", "WATCH")["dir"] == "cool"
    assert cb.buyer_note("WATCH", "BUY")["dir"] == "heat"
    assert cb.buyer_note("BUY", "BUY") is None


def test_buyer_note_avoids_the_index_terminology_trap():
    """'매수자 우위' 라고 쓰면 안 된다.

    KB '매수우위지수' 는 매수 문의가 많다는 뜻이라 **높을수록 시장이 뜨겁다.**
    등급 하향(=지수 하락)을 "매수자 우위로 이동" 이라고 쓰면 앱 자신의 지표와
    정반대로 읽힌다.
    """
    for a, b in (("STRONG_BUY", "WATCH"), ("WATCH", "BUY")):
        note = cb.buyer_note(a, b)
        blob = note["label"] + note["why"]
        assert "매수자 우위" not in blob
        assert "매도자 우위" not in blob


def test_buyer_note_is_not_a_prediction():
    """반가치 — 확신형 예측·과장 금지(DESIGN.md 보이스)."""
    for a, b in (("STRONG_BUY", "WATCH"), ("WATCH", "STRONG_BUY")):
        blob = "".join(cb.buyer_note(a, b).values())
        for banned in ("기회", "오릅니다", "떨어집니다", "!", "지금이", "확실"):
            assert banned not in blob, f"{banned} 가 들어갔다: {blob}"


def test_front_and_server_labels_match():
    """같은 변화를 두 화면이 다르게 말하면 앱이 서로 다른 말을 한다."""
    html = pathlib.Path("src/realty_signal/web/index.html").read_text(encoding="utf-8")
    m = re.search(r"function _buyerNoteOf\(frm, to\)\{(.*?)\n\}", html, re.S)
    assert m, "프론트 _buyerNoteOf 미발견"
    front = m.group(1)
    for a, b in (("STRONG_BUY", "WATCH"), ("WATCH", "BUY")):
        note = cb.buyer_note(a, b)
        assert note["label"] in front, f"프론트에 '{note['label']}' 라벨이 없다"
        assert note["why"] in front, "why 문구가 서버와 다르다"
