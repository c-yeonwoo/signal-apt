"""User-facing strategy cards must never revive an unsafe raw BUY grade."""

import pandas as pd
from types import SimpleNamespace

from realty_signal import api


def test_display_map_keeps_regions_but_holds_missing_or_unsafe_assessments(monkeypatch):
    monkeypatch.setattr(api, "_signal_map", lambda: {"준비구": "BUY", "보류구": "STRONG_BUY",
                                                  "누락구": "BUY"})
    monkeypatch.setattr(api.md, "assessed_signal_labels", lambda today: {
        "준비구": {"display_signal": "BUY", "assessment_status": "ready"},
        "보류구": {"display_signal": "STRONG_BUY", "assessment_status": "held"},
    })
    assert api._display_signal_map() == {"준비구": "BUY", "보류구": "HELD", "누락구": "HELD"}


def test_display_map_fails_closed_when_assessment_unavailable(monkeypatch):
    monkeypatch.setattr(api, "_signal_map", lambda: {"노원구": "STRONG_BUY"})
    def unavailable(_today):
        raise RuntimeError("assessment unavailable")
    monkeypatch.setattr(api.md, "assessed_signal_labels", unavailable)
    assert api._display_signal_map() == {"노원구": "HELD"}


def test_strategy_cards_do_not_promote_held_raw_buy(monkeypatch):
    monkeypatch.setattr(api, "_signal_map", lambda: {"후보구": "STRONG_BUY"})
    monkeypatch.setattr(api.md, "assessed_signal_labels", lambda today: {
        "후보구": {"display_signal": "HELD", "assessment_status": "held"}})
    localities = pd.DataFrame([{"region": "후보구", "price": 2000, "저평가도": 5, "입지점수": 50}])
    monkeypatch.setattr(api.store, "load_localities", lambda: localities)
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {"현재구": {"급지": "C"},
                                                       "후보구": {"급지": "B"}}})
    monkeypatch.setattr(api, "_max_purchase", lambda *args: (100_000, {}))
    monkeypatch.setattr(api, "_build_listings", lambda *args, **kwargs: [])

    assert api.undervalued()["listings"][0]["시그널"] == "HELD"
    result = api.tradeup("현재구", current_value=50_000)
    assert result["cards"][0]["시그널"] == "HELD"


def test_myfeed_recalculates_old_cached_complex_grade_under_current_hold(monkeypatch):
    monkeypatch.setattr(api, "_uid", lambda request: 1)
    monkeypatch.setattr(api.db, "fav_list", lambda uid: [
        {"kind": "region", "key": "노원구"},
        {"kind": "complex", "key": "노원구|테스트단지"},
    ])
    monkeypatch.setattr(api, "_display_signal_map", lambda: {"노원구": "HELD"})
    monkeypatch.setattr(api, "_personal_listings_allowed", lambda **kwargs: False)
    monkeypatch.setattr(api, "_presale", lambda: [])
    monkeypatch.setattr(api, "_code_of", lambda region: "11350")
    monkeypatch.setattr(api.db, "kv_get", lambda *args, **kwargs: {
        "총거래": 8, "단지시그널": {"등급": "STRONG_BUY", "점수": 80},
        "평형별": [], "매매추이": [],
    })
    monkeypatch.setattr(api, "_kb", lambda: SimpleNamespace(last_date=pd.Timestamp("2026-09-28")))
    items = api.myfeed(object())["items"]
    assert items[0]["signal"] == "HELD"
    assert items[1]["단지등급"] == "HELD"
    assert items[1]["단지점수"] is None
