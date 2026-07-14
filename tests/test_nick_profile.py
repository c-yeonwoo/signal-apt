"""Nick 프로필 주입 · 소프트 한도 단위 테스트."""

from __future__ import annotations

from realty_signal import advisor, db


def test_build_system_empty():
    s = advisor.build_system({}, {})
    assert "Nick" in s
    assert "사용자 프로필(답변 시 참고)" not in s


def test_build_system_with_profile_and_favs():
    s = advisor.build_system(
        {"가용자본": 30000, "거주지": "마포구", "주택수": 0, "관심평수": [25, 34]},
        {"관심지역": ["마포구", "성동구"], "관심단지": ["공덕자이"]},
    )
    assert "사용자 프로필(답변 시 참고)" in s
    assert "가용자본 3.0억" in s
    assert "거주지 마포구" in s
    assert "관심지역 마포구, 성동구" in s
    assert "관심단지 공덕자이" in s
    assert "예산·거주지·관심지역을 우선 고려" in s


def test_usage_inc_and_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "u.db")
    db._migrated[0] = False
    assert db.usage_get(1, "nick") == 0
    assert db.usage_inc(1, "nick") == 1
    assert db.usage_inc(1, "nick") == 2
    assert db.usage_get(1, "nick") == 2
    assert db.usage_get(1, "report") == 0
