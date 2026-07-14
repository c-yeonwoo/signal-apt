"""동네 리포트 스냅샷·diff 단위 테스트."""

from __future__ import annotations

from realty_signal import db
from realty_signal.api import _nbhd_diff, _nbhd_metrics


def test_nbhd_diff_signal_and_numeric():
    curr = {"시그널": "BUY", "전세수급": 120, "평단가": 5000, "급매": 3, "국면": "회복"}
    prev = {"시그널": "WATCH", "전세수급": 110, "평단가": 5000, "급매": 1, "국면": "회복"}
    d = _nbhd_diff(curr, prev)
    keys = {x["key"]: x for x in d}
    assert "시그널" in keys and keys["시그널"]["delta"] > 0
    assert "전세수급" in keys and keys["전세수급"]["delta"] == 10
    assert "급매" in keys and keys["급매"]["delta"] == 2
    assert "평단가" not in keys  # unchanged
    assert "국면" not in keys


def test_nbhd_snap_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "n.db")
    db._migrated[0] = False
    db.nbhd_snap_save(1, "마포구", "2026-W28", {"시그널": "BUY", "기준일": "2026-07-07"})
    db.nbhd_snap_save(1, "마포구", "2026-W29", {"시그널": "STRONG_BUY", "기준일": "2026-07-14"})
    assert db.nbhd_snap_get(1, "마포구", "2026-W29")["시그널"] == "STRONG_BUY"
    prev = db.nbhd_snap_prev(1, "마포구", "2026-W29")
    assert prev["week"] == "2026-W28"
    assert prev["data"]["시그널"] == "BUY"
    assert db.nbhd_snap_weeks(1, "마포구") == ["2026-W29", "2026-W28"]


def test_nbhd_metrics_extract():
    m = _nbhd_metrics({
        "시그널": "BUY", "전세수급": 100, "매물": {"급매": 2, "청약": 1},
        "국면": {"phase": "상승"}, "평단가": 4000,
    })
    assert m["급매"] == 2 and m["국면"] == "상승" and m["평단가"] == 4000
