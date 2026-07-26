"""임장 코스 — 순서·시각·체크리스트 채점."""

from __future__ import annotations

from datetime import date

import pytest

from realty_signal.services import imjang as ij

CANDS = [
    {"단지": "북단지", "region": "노원구", "예상가": 90000, "급지": 1, "근거": "노원구"},
    {"단지": "남단지", "region": "금천구", "예상가": 88000, "급지": 1, "근거": "금천구"},
    {"단지": "중단지", "region": "구로구", "예상가": 85000, "급지": 2, "근거": "구로구"},
]
COORDS = {                       # 북 → 중 → 남 순으로 내려오는 배치
    "노원구 북단지": (37.65, 127.05),
    "구로구 중단지": (37.50, 126.89),
    "금천구 남단지": (37.45, 126.90),
}


@pytest.fixture(autouse=True)
def stub(monkeypatch, tmp_path):
    from realty_signal import db

    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    monkeypatch.setattr(ij, "_coords", lambda cands: COORDS)
    monkeypatch.setattr(ij, "_move_min", lambda a, b: 20)


def test_course_has_clock_times_in_order():
    c = ij.build_course(CANDS, start="10:00", stop_min=50, on="2026-08-01")
    assert c["ready"] and len(c["stops"]) == 3
    assert c["stops"][0]["도착"] == "10:00"
    assert c["stops"][0]["출발"] == "10:50"
    assert c["stops"][1]["도착"] == "11:10"      # 50분 체류 + 20분 이동
    assert c["종료"] == "13:10"
    assert c["총소요"] == 190
    assert c["이동합"] == 40                      # 첫 정거장은 집 좌표가 없어 이동 0


def test_home_adds_first_leg():
    c = ij.build_course(CANDS, start="10:00", stop_min=50, on="2026-08-01",
                        home=(37.60, 127.00))
    assert c["stops"][0]["이동"] == 20
    assert c["stops"][0]["도착"] == "10:20"
    assert c["이동합"] == 60


def test_order_is_nearest_neighbour_from_home():
    south = ij.build_course(CANDS, on="2026-08-01", home=(37.44, 126.90))
    assert [s["단지"] for s in south["stops"]] == ["남단지", "중단지", "북단지"]
    north = ij.build_course(CANDS, on="2026-08-01", home=(37.68, 127.06))
    assert [s["단지"] for s in north["stops"]] == ["북단지", "중단지", "남단지"]


def test_visited_complexes_go_last():
    c = ij.build_course(CANDS, on="2026-08-01", home=(37.68, 127.06),
                        visited={"노원구|북단지": {"방문일": "2026-07-25"}})
    assert c["stops"][-1]["단지"] == "북단지"
    assert c["stops"][-1]["방문일"] == {"방문일": "2026-07-25"}


def test_default_date_is_upcoming_saturday():
    c = ij.build_course(CANDS)
    d = date.fromisoformat(c["날짜"])
    assert d.weekday() == 5 and d > date.today()


def test_bad_time_falls_back_to_default():
    c = ij.build_course(CANDS, start="25:99", on="2026-08-01")
    assert c["ready"] and c["출발"] == ij.DEFAULT_START


def test_empty_candidates():
    assert ij.build_course([]) == {"ready": False, "reason": "no_candidates"}


def test_stops_capped():
    many = [{"단지": f"단지{i}", "region": "노원구"} for i in range(8)]
    assert len(ij.build_course(many, on="2026-08-01")["stops"]) == ij.MAX_STOPS


def test_score_uses_answered_items_only():
    assert ij.score({"walk": 2, "gap": 2}) == {"점수": 100, "응답": 2, "전체": len(ij.CHECKS)}
    assert ij.score({"walk": 2, "gap": 0}) == {"점수": 50, "응답": 2, "전체": len(ij.CHECKS)}
    assert ij.score({}) is None
    assert ij.score({"모르는항목": 2}) is None


def test_weak_points_lists_bad_labels():
    bad = ij.weak_points({"walk": 0, "noise": 0, "gap": 2})
    assert "역까지 실측 도보" in bad and "소음·냄새" in bad
    assert "동간거리·일조" not in bad


def test_clean_checks_drops_unknown_and_clamps():
    assert ij.clean_checks({"walk": 9, "gap": -3, "hack": 2, "noise": "x"}) == {"walk": 2, "gap": 0}
