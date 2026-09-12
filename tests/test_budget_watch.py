"""내 예산 안에 새로 들어온 매물 (R3).

'새로 들어옴' 은 세 가지 다른 사건이다 — 신규 등장 / 가격 인하 / 매수력 상향.
**뭉개면 거짓이 된다.** 그래서 원인을 구분해 붙인다.
"""

from __future__ import annotations

import pathlib
import re

from realty_signal import db
from realty_signal.services import budget_watch as bw

BUDGET = 90_000.0


def _row(key, total, name=None, kind="급매"):
    return {"key": key, "총액": total, "유형": kind, "단지명": name or key,
            "지역": "노원구", "시그널": "BUY", "평형": "24", "지표값": -8.0}


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False


def test_first_run_is_quiet_but_says_why(tmp_path, monkeypatch):
    """비교 대상이 없으면 '새로 들어옴' 을 말할 수 없다 — 0 을 이유 없이 비우지 않는다."""
    _setup(tmp_path, monkeypatch)
    out = bw.compute(1, [_row("a", 80_000)], BUDGET)
    assert out["ready"] is False and out["reason"] == "first_run"
    assert out["in_budget"] == 1


def test_no_budget_is_not_computed(tmp_path, monkeypatch):
    """예산이 없으면 '예산 안' 이라는 말 자체가 무의미하다."""
    _setup(tmp_path, monkeypatch)
    assert bw.compute(1, [_row("a", 80_000)], None)["reason"] == "no_budget"
    assert bw.compute(1, [_row("a", 80_000)], 0)["reason"] == "no_budget"


def test_new_listing_is_labelled_new(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    base = [_row("a", 80_000)]
    bw.mark_seen(1, base, BUDGET)
    out = bw.compute(1, base + [_row("b", 85_000, "새매물")], BUDGET)
    assert out["entered_total"] == 1
    assert out["entered"][0]["cause"] == "new" and out["entered"][0]["cause_ko"] == "신규 등장"


def test_price_cut_entry_is_distinguished_from_new(tmp_path, monkeypatch):
    """있었지만 예산을 넘던 매물이 내려온 것은 '신규' 가 아니다."""
    _setup(tmp_path, monkeypatch)
    bw.mark_seen(1, [_row("a", 80_000), _row("b", 100_000)], BUDGET)   # b 는 예산 밖(1.3배 내)
    out = bw.compute(1, [_row("a", 80_000), _row("b", 88_000)], BUDGET)
    e = out["entered"][0]
    assert e["key"] == "b" and e["cause"] == "price_cut"
    assert e["before"] == 100_000 and e["drop"] == 12_000


def test_budget_increase_is_not_called_a_price_cut(tmp_path, monkeypatch):
    """내 예산이 올라서 들어온 것을 '가격 인하' 라고 하면 거짓이다."""
    _setup(tmp_path, monkeypatch)
    bw.mark_seen(1, [_row("b", 100_000)], BUDGET)
    out = bw.compute(1, [_row("b", 100_000)], 110_000.0)
    e = out["entered"][0]
    assert e["cause"] == "budget_up" and e["cause_ko"] == "매수력 상향"


def test_leaving_is_split_into_over_budget_and_gone(tmp_path, monkeypatch):
    """예산을 넘어 빠진 것과 아예 사라진 것은 다른 사건이다(거래 완료 가능)."""
    _setup(tmp_path, monkeypatch)
    bw.mark_seen(1, [_row("a", 80_000), _row("c", 85_000)], BUDGET)
    out = bw.compute(1, [_row("a", 95_000)], BUDGET)   # a 는 초과, c 는 사라짐
    causes = {x["cause"] for x in out["left"]}
    assert causes == {"over", "gone"}


def test_items_outside_prev_budget_are_not_counted_as_leaving(tmp_path, monkeypatch):
    """애초에 예산 밖이던 매물이 사라진 건 '빠짐' 이 아니다."""
    _setup(tmp_path, monkeypatch)
    bw.mark_seen(1, [_row("a", 80_000), _row("far", 115_000)], BUDGET)
    out = bw.compute(1, [_row("a", 80_000)], BUDGET)
    assert out["left_total"] == 0


def test_price_change_alone_does_not_look_like_a_new_listing(tmp_path, monkeypatch):
    """식별자에 호가가 섞이면 가격이 바뀔 때마다 '새 매물' 이 된다 — 그걸 막는다."""
    _setup(tmp_path, monkeypatch)
    bw.mark_seen(1, [_row("a", 80_000)], BUDGET)
    out = bw.compute(1, [_row("a", 75_000)], BUDGET)
    assert out["entered_total"] == 0
    assert out["cheaper_total"] == 1 and out["cheaper"][0]["drop"] == 5_000


def test_unpriced_listings_are_never_in_budget(tmp_path, monkeypatch):
    """가격 미상(청약 등)을 '예산 안' 으로 치지 않는다 — N2 와 같은 원칙."""
    _setup(tmp_path, monkeypatch)
    rows = [_row("a", 80_000), {"key": "sub", "총액": None, "유형": "청약", "단지명": "미정"}]
    bw.mark_seen(1, rows, BUDGET)
    out = bw.compute(1, rows, BUDGET)
    assert out["in_budget"] == 1


def test_uses_its_own_snapshot_key():
    """다른 기능과 스냅샷을 공유하면 서로 지운다(복귀 브리핑과 같은 규율)."""
    from realty_signal.services import comeback
    assert bw.KV_PREFIX != comeback.KV_PREFIX
    assert bw.KV_PREFIX != "briefing_snap:"


def test_listing_key_is_price_independent():
    """`_build_listings` 의 key 에 호가가 들어가면 안 된다."""
    from realty_signal import api

    raw = {"naver_id": "N1", "호가": 50_000, "평형": "24", "층": "5/15"}
    ref = {"평형": "24", "호가": 50_000, "naver_id": "N1"}
    k1 = api._listing_key("급매", raw, ref, "가", "노원구")
    k2 = api._listing_key("급매", {**raw, "호가": 70_000}, {**ref, "호가": 70_000}, "가", "노원구")
    assert k1 == k2 == "급매:N1"


def test_route_defers_snapshot_until_seen_ack():
    """GET 응답은 아직 열람이 아니다 — 브라우저 ack 전에는 기준점을 옮기지 않는다."""
    home = (pathlib.Path(__file__).resolve().parents[1] / "src/realty_signal/routes/home.py").read_text(encoding="utf-8")
    m = re.search(r"def budget_watch\(request: Request\):.*?(?=\n@router|\Z)", home, re.S)
    assert m
    src = m.group(0)
    assert "bw.mark_seen(" not in src
    assert '@router.post("/api/budget-watch/seen")' in home
