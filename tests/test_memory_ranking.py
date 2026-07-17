"""Nick memory · engagement ranking 테스트."""

from __future__ import annotations

from realty_signal import db
from realty_signal.brain import memory, ranking
from realty_signal import advisor


def test_extract_regions_and_complexes():
    ex = memory.extract_from_text(
        "마포구 공덕자이 아파트랑 성동구 타이밍 어때?",
        known_regions=["마포구", "성동구", "강남구"],
    )
    assert "마포구" in ex["regions"]
    assert "성동구" in ex["regions"]
    assert any("공덕자이" in c for c in ex["complexes"])


def test_memory_update_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "m.db")
    db._migrated[0] = False
    mem = memory.update_from_messages(
        1,
        [{"role": "user", "text": "강남구 급매 있어?"}],
        known_regions=["강남구", "마포구"],
        answer="강남구는 BUY 구간입니다.",
    )
    assert "강남구" in mem["regions"]
    assert mem["turns"] == 1
    assert memory.load(1)["regions"]
    block = memory.format_for_system(mem)
    assert "최근 대화" in block
    assert "강남구" in block
    memory.clear(1)
    assert memory.load(1)["regions"] == []


def test_build_system_with_memory():
    s = advisor.build_system(
        {"거주지": "마포구"},
        {"관심지역": ["마포구"]},
        memory={"regions": ["성동구"], "complexes": [], "notes": ["성동구 전세가율?"], "turns": 2},
    )
    assert "최근 대화 기억" in s
    assert "성동구" in s


def test_engagement_bonus(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "r.db")
    db._migrated[0] = False
    db.event_log(1, "listing_detail_open", {"region": "강남구", "kind": "급매"})
    db.event_log(1, "listing_detail_open", {"region": "강남구", "kind": "급매"})
    db.event_log(1, "listing_click", {"region": "마포구", "kind": "경매"})
    scores = ranking.engagement_scores(uid=1)
    assert "강남구" in scores
    listings = [
        {"지역": "강남구", "유형": "급매", "타이밍점수": 70, "기회도": 70, "타이밍근거": "급매갭"},
        {"지역": "부산", "유형": "급매", "타이밍점수": 70, "기회도": 70},
    ]
    out = ranking.apply_engagement_bonus(listings, scores)
    assert out[0]["타이밍점수"] > 70
    assert out[0].get("engagement_bonus", 0) > 0
    assert out[1]["타이밍점수"] == 70
