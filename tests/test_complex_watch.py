"""관심단지 변화(R5) — 거짓 변화를 만들지 않는지가 핵심.

24개월 롤링 윈도·신고 지연·시도 단위 등록 같은 함정이 다 '가짜 뉴스'로 이어진다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from realty_signal import db
from realty_signal.services import complex_watch as cw

_HTML = Path(__file__).resolve().parents[1] / "src/realty_signal/web/index.html"


def _data(*, months, ppy, amt, jeonse=None, flat=23, cnt=100):
    return {
        "단지명": "테스트아파트",
        "매매추이": [{"ym": ym, "평단가": ppy, "건수": n} for ym, n in months],
        "평형별": [{"평형": flat, "전용㎡": 76.8, "최근매매": amt, "평단가": ppy,
                    "매매건수": cnt, "최근전세": jeonse,
                    "전세가율": round(jeonse / amt * 100) if (jeonse and amt) else None,
                    "갭": (amt - jeonse) if (amt and jeonse) else None}],
        "최근평단가": ppy, "총거래": cnt,
    }


def test_rolling_window_does_not_fake_new_trades():
    """오래된 달이 빠져 총거래가 줄어도 '새 거래' 로 세지 않는다."""
    prev = cw.snapshot_of(_data(months=[("2026-05", 3), ("2026-06", 4), ("2026-07", 2)],
                                ppy=4000, amt=100000))
    # 윈도가 한 달 굴러 05가 빠지고 08이 들어왔다 — 새 거래는 08의 1건뿐
    cur = cw.snapshot_of(_data(months=[("2026-06", 4), ("2026-07", 2), ("2026-08", 1)],
                               ppy=4000, amt=100000))
    ch = {c["kind"]: c for c in cw.diff_one(prev, cur)}
    assert ch["new_trade"]["건수"] == 1
    assert ch["new_trade"]["월"] == ["2026-08"]


def test_unknown_older_month_is_not_counted():
    """스냅샷이 기억하지 못하는 달의 차이는 '모른다' — 새 거래가 아니다."""
    prev = cw.snapshot_of(_data(months=[("2026-07", 2)], ppy=4000, amt=100000))
    cur = cw.snapshot_of(_data(months=[("2026-05", 9), ("2026-06", 9), ("2026-07", 2)],
                               ppy=4000, amt=100000))
    assert cw.diff_one(prev, cur) == []


def test_late_report_counts_as_news_with_caveat():
    """지난달 건수가 늘어난 것도 새 소식이지만, 신고 지연을 밝혀야 한다."""
    prev = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=4000, amt=100000))
    cur = cw.snapshot_of(_data(months=[("2026-08", 4)], ppy=4000, amt=100000))
    nt = cw.diff_one(prev, cur)[0]
    assert nt["kind"] == "new_trade" and nt["건수"] == 3
    assert "신고" in nt["note"]


def test_noise_below_threshold_is_silent():
    prev = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=4000, amt=100000))
    cur = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=4010, amt=100500))
    assert cw.diff_one(prev, cur) == []


def test_price_and_gap_changes():
    prev = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=4000, amt=100000, jeonse=50000))
    cur = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=4400, amt=110000, jeonse=50000))
    kinds = {c["kind"] for c in cw.diff_one(prev, cur)}
    assert {"ppy", "amt", "gap"} <= kinds


def test_main_flat_shift_does_not_compare_amounts():
    """주력 평형이 바뀌면 금액 비교는 무의미하다 — 사실만 알린다."""
    prev = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=4000, amt=100000, flat=23))
    cur = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=4000, amt=180000, flat=34))
    kinds = {c["kind"] for c in cw.diff_one(prev, cur)}
    assert kinds == {"flat_shift"}


def test_budget_crossing():
    prev = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=4000, amt=100000))
    cur = cw.snapshot_of(_data(months=[("2026-08", 1)], ppy=3600, amt=90000))
    kinds = {c["kind"] for c in cw.diff_one(prev, cur, budget=93000)}
    assert "budget_in" in kinds
    kinds = {c["kind"] for c in cw.diff_one(cur, prev, budget=93000)}
    assert "budget_out" in kinds


def test_unqueryable_payload_never_looks_quiet():
    """조회 자체가 안 되는 응답을 빈 스냅샷으로 통과시키면 매주 '변화 없음' 이라 거짓말한다."""
    assert cw.snapshot_of({"_unavailable": "시·도 단위"}) == {}
    assert cw.snapshot_of({"지원안함": True, "평형별": [], "매매추이": []}) == {}
    assert cw.snapshot_of({"거래없음": True, "평형별": [], "매매추이": []})["none"] is True


def test_compute_separates_unavailable_from_quiet(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")

    def loader(region, name):
        if name == "조회불가":
            return {"_unavailable": "시·도 단위로 등록됨"}, None
        return _data(months=[("2026-08", 2)], ppy=4000, amt=100000), 1_700_000_000

    favs = [("강남구", "테스트아파트"), ("서울", "조회불가")]
    out = cw.compute(1, favs, loader)
    assert out["ready"] is True
    assert [u["단지명"] for u in out["unavailable"]] == ["조회불가"]
    # 첫 회차는 '변화 없음' 이 아니라 '첫 기록'
    assert "첫 기록" in out["quiet_reason"]


def test_snapshot_advances_only_after_show(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")

    def loader(region, name):
        return _data(months=[("2026-08", 2)], ppy=4000, amt=100000), 1_700_000_000

    favs = [("강남구", "테스트아파트")]
    out = cw.compute(1, favs, loader)
    assert db.kv_get(cw.KV_PREFIX + "1") is None      # 아직 안 옮겼다
    cw.mark_seen(1, out["_snaps"])
    assert db.kv_get(cw.KV_PREFIX + "1")["items"]

    def loader2(region, name):
        return _data(months=[("2026-08", 5)], ppy=4400, amt=100000), 1_700_000_000

    out2 = cw.compute(1, favs, loader2)
    kinds = {c["kind"] for c in out2["moved"][0]["changes"]}
    assert {"new_trade", "ppy"} <= kinds


def test_no_favorites_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    assert cw.compute(1, [], lambda r, n: (None, None))["reason"] == "no_favorites"


def test_html_renders_complex_watch():
    src = _HTML.read_text(encoding="utf-8")
    assert "_loadComplexWatch" in src
    assert 'id="dashCxWrap"' in src
    assert "/api/complex-watch" in src


def test_loader_rejects_sido_level_region(tmp_path, monkeypatch):
    """'서울' 로 등록된 관심단지는 국토부 실거래를 조회할 수 없다 — 이유를 말해야 한다."""
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    load = cw.cache_loader()
    data, ts = load("서울", "상계주공9단지아파트")
    assert ts is None
    assert "시군구로 다시 등록" in data["_unavailable"]
    # 시군구는 통과해야 한다(캐시가 비어 있으면 '미수집' 이라고 말한다)
    data, _ = load("강남구", "은마아파트")
    assert "수집" in data["_unavailable"]


def test_loader_never_calls_network(monkeypatch, tmp_path):
    """홈 카드가 관심단지 수만큼 국토부 API 를 때리면 홈이 느려진다."""
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    from realty_signal.ingest import complex as cx
    monkeypatch.setattr(cx, "fetch_complex",
                        lambda *a, **k: pytest.fail("loader 가 네트워크를 호출했다"))
    cw.cache_loader()("강남구", "은마아파트")


def test_briefing_keeps_its_own_baseline():
    """브리핑과 홈이 스냅샷을 공유하면 브리핑이 나갈 때마다 홈의 변화가 사라진다."""
    import inspect

    from realty_signal import briefing
    src = inspect.getsource(briefing.build)
    assert 'prev.get("complexes")' in src          # 브리핑 자기 스냅샷을 기준점으로
    assert "cw.compute(" not in src                # 홈 전용 kv 를 건드리지 않는다
    assert '"complexes": cx_snaps' in src          # 자기 스냅샷에만 저장


def test_briefing_renders_complex_section_with_lag_caveat():
    import inspect

    from realty_signal import briefing
    src = inspect.getsource(briefing._render)
    assert "[관심단지]" in src
    assert "30일 내 신고" in src
