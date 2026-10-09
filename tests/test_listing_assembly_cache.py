"""매물 목록은 원본 파일이 그대로면 다시 조립하지 않고, 경매 갱신은 응답을 막지 않는다."""

import os
import time

from realty_signal import api, auction
from realty_signal.brain import ranking
from realty_signal.ingest import external
from realty_signal.routes import auction as auction_routes


def _row():
    return {"유형": "경매", "단지명": "가", "지역": "노원구", "시그널": "HELD",
            "기회도": 3, "key": "경매:1", "총액": 1, "ref": {}, "원시시그널": "BUY"}


def _quiet(monkeypatch, build):
    monkeypatch.setattr(api, "_build_listings", build)
    monkeypatch.setattr(api, "_personal_listings_allowed", lambda **_kwargs: False)
    monkeypatch.setattr(api, "_uid", lambda _request: None)
    monkeypatch.setattr(api, "_attach_card_lines", lambda items, _uid: items)
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-05")
    monkeypatch.setattr(api, "_data_age_days", lambda: 1)
    monkeypatch.setattr(ranking, "engagement_scores", lambda **_kwargs: {})
    monkeypatch.setattr(external, "schedule_hank_refresh", lambda: None)


def test_assembled_listings_reuse_until_a_source_file_changes(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    api._listing_assembly_cache.clear()
    hank = tmp_path / "hank.json"
    hank.write_text("{}")
    monkeypatch.setattr(external, "cache_path", lambda: hank)
    calls = []

    def build(kinds, include_private=False):
        calls.append(sorted(kinds))
        return [_row()]

    _quiet(monkeypatch, build)
    try:
        first = api.listings_all(None, "경매")
        second = api.listings_all(None, "경매")
        assert calls == [["경매"]]
        assert first["listings"][0]["단지명"] == second["listings"][0]["단지명"] == "가"
        assert "원시시그널" not in first["listings"][0]
        first["listings"][0]["단지명"] = "바뀜"
        assert api.listings_all(None, "경매")["listings"][0]["단지명"] == "가"
        os.utime(hank, (time.time() + 5, time.time() + 5))
        api.listings_all(None, "경매")
        assert calls == [["경매"], ["경매"]]
    finally:
        api._listing_assembly_cache.clear()


def test_presale_and_redev_lists_are_rebuilt_every_read(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    api._listing_assembly_cache.clear()
    calls = []

    def build(kinds, include_private=False):
        calls.append(sorted(kinds))
        return [{**_row(), "유형": "청약", "key": "청약:1"}]

    _quiet(monkeypatch, build)
    try:
        api.listings_all(None, "청약")
        api.listings_all(None, "재건축")
        api.listings_all(None, "청약")
        assert calls == [["청약"], ["재건축"], ["청약"]]
    finally:
        api._listing_assembly_cache.clear()


def test_card_view_skips_unused_decision_envelope_without_changing_default(monkeypatch):
    _quiet(monkeypatch, lambda *_args, **_kwargs: [_row()])
    calls = []

    def annotate(rows, _uid):
        calls.append(1)
        return [{**row, "decision": {"id": "detail"}, "lines": {"cash": "detail"}}
                for row in rows]

    monkeypatch.setattr(api, "_attach_card_lines", annotate)
    compact = api.listings_all(None, "경매", view="card")["listings"][0]
    full = api.listings_all(None, "경매")["listings"][0]
    assert calls == [1]
    assert "decision" not in compact and "lines" not in compact
    assert compact["시그널"] == full["시그널"] == "HELD"
    assert compact["key"] == full["key"]
    assert full["decision"]["id"] == "detail"


def test_hank_refresh_stays_off_the_listing_response(monkeypatch):
    called = []
    monkeypatch.setattr(external, "hank_due", lambda path=None: True)
    monkeypatch.setattr(external, "refresh_hank_cache", lambda *args, **kwargs: called.append(1))
    external.schedule_hank_refresh()
    assert called == []

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    external._REFRESH_RUNNING = False
    external.schedule_hank_refresh()
    deadline = time.time() + 2
    while not called and time.time() < deadline:
        time.sleep(0.02)
    assert called == [1]


def test_auction_screen_does_not_wait_for_hank(monkeypatch):
    seen = []
    monkeypatch.setattr(external, "schedule_hank_refresh", lambda: seen.append("sched"))
    monkeypatch.setattr(external, "ensure_hank_cache", lambda: seen.append("block"))
    monkeypatch.setattr(api, "_auction_signal_map", lambda: {})
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(auction, "load", lambda: [])
    monkeypatch.setattr(external, "read_hank_cards", lambda: [])
    auction_routes.auction_listings()
    assert seen == ["sched"]
