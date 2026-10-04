"""Price-change evidence is written on successful refreshes and read cache-only."""

from copy import deepcopy

from realty_signal import db
from realty_signal.services import listing_price_history


def _source_row(identifier="same-id", price=50_000, observed=1_800_000_000, **kw):
    return {"naver_id": identifier, "호가": price, "fetched_at": observed, **kw}


def _current_row(identifier="same-id", price=50_000, observed=1_800_000_000, **kw):
    return {"key": "급매:same-id", "유형": "급매", "source": "baroezip",
            "price_kind": "asking", "총액": price, "fetched_at": observed,
            "ref": {"naver_id": identifier}, **kw}


def test_only_changed_prices_of_fresh_rows_are_recorded_with_hashed_ids():
    now = 1_800_000_000
    assert listing_price_history.record_source_rows("baroezip", [_source_row(observed=now)], now=now) == 1
    assert listing_price_history.record_source_rows("baroezip", [_source_row(observed=now + 1)], now=now + 1) == 0
    assert listing_price_history.record_source_rows("baroezip", [
        _source_row("stale", 60_000, now + 2, stale=True),
        _source_row("degraded", 60_000, now + 2, degraded=True),
        _source_row("missing-id", 60_000, now + 2, naver_id=None),
        _source_row("bad-price", float("nan"), now + 2),
    ], now=now + 2) == 0
    assert listing_price_history.record_source_rows(
        "baroezip", [_source_row(price=48_000, observed=now + 86400)], now=now + 86400) == 1

    c = db.conn()
    try:
        hashes = {row[0] for row in c.execute("SELECT listing_hash FROM listing_price_history_v1")}
        assert len(hashes) == 1 and "same-id" not in next(iter(hashes))
        assert c.execute("SELECT COUNT(*) FROM listing_price_history_v1").fetchone()[0] == 2
    finally:
        c.close()


def test_lower_same_listing_quote_is_attached_as_observation_not_sale_or_discount():
    now = 1_800_000_000
    listing_price_history.record_source_rows("baroezip", [_source_row(price=50_000, observed=now - 86400)], now=now)
    listing_price_history.record_source_rows("baroezip", [_source_row(price=48_000, observed=now)], now=now)
    rows = [_current_row(price=48_000, observed=now),
            _current_row("stale", 60_000, now, stale=True),
            {"유형": "경매", "총액": 30_000}]
    before = deepcopy(rows)
    result = listing_price_history.attach(rows, now=now)
    assert rows == before
    assert result[0]["price_reduction"] == {
        "상태": "수집호가인하관측", "이전호가": 50_000, "현재호가": 48_000,
        "차액": -2_000, "차이율": -4.0, "관측간일수": 1, "확인시각": now, "관측횟수": 2,
        "기준": "동일 원천 매물의 서로 다른 수집 시점 호가",
        "안내": "수집 호가 변화입니다. 실제 거래가·판매 상태·인하 이유는 확인되지 않았습니다.",
    }
    assert result[1]["price_reduction"] is None
    assert "price_reduction" not in result[2]
    c = db.conn()
    try:
        assert c.execute("SELECT COUNT(*) FROM listing_price_history_v1").fetchone()[0] == 2
    finally:
        c.close()


def test_history_is_separated_by_source_and_requires_current_latest_price_match():
    now = 1_800_000_000
    listing_price_history.record_source_rows("baroezip", [_source_row(price=50_000, observed=now - 86400)], now=now)
    listing_price_history.record_source_rows("baroezip", [_source_row(price=48_000, observed=now)], now=now)
    listing_price_history.record_source_rows("hanbang", [
        {"hanbang_id": "same-id", "호가": 55_000, "fetched_at": now - 86400}], now=now)
    listing_price_history.record_source_rows("hanbang", [
        {"hanbang_id": "same-id", "호가": 54_000, "fetched_at": now}], now=now)
    wrong_price = _current_row(price=47_000, observed=now)
    wrong_price["source"] = "baroezip"
    wrong_source = _current_row(price=54_000, observed=now)
    wrong_source["source"] = "hanbang"
    wrong_source["ref"] = {"hanbang_id": "same-id"}
    result = listing_price_history.attach([wrong_price, wrong_source], now=now)
    assert result[0]["price_reduction"] is None
    assert result[1]["price_reduction"]["이전호가"] == 55_000
    assert result[1]["price_reduction"]["현재호가"] == 54_000


def test_history_retention_prunes_old_rows_only_after_refresh():
    now = 1_800_000_000
    c = db.conn()
    try:
        c.execute("INSERT INTO listing_price_history_v1 VALUES(?,?,?)",
                  ("old-hash", now - listing_price_history.RETENTION_SECONDS - 1, 50_000))
        c.commit()
    finally:
        c.close()
    listing_price_history.record_source_rows("baroezip", [_source_row(observed=now)], now=now)
    c = db.conn()
    try:
        assert c.execute("SELECT COUNT(*) FROM listing_price_history_v1 WHERE listing_hash='old-hash'").fetchone()[0] == 0
    finally:
        c.close()


def test_quicksale_refresh_records_only_a_successfully_published_scan(tmp_path, monkeypatch):
    from realty_signal import api

    now = 1_800_000_000
    path = tmp_path / "quicksale.json"
    row = _source_row(price=49_000, observed=now)
    monkeypatch.setattr(api, "QUICKSALE_FILE", path)
    monkeypatch.setattr(api, "_scan_regions", lambda: ["노원구"])
    monkeypatch.setattr(api, "_radar_scan_with_status", lambda *_a, **_kw:
                        ([row], {"usable": True, "queryable_regions": 1,
                                 "successful_regions": ["노원구"]}))
    monkeypatch.setattr(api, "_preserve_unscanned", lambda _path, rows, _scan: rows)
    monkeypatch.setattr(api, "_record_radar_refresh", lambda *_a: None)
    monkeypatch.setattr("realty_signal.services.listing_price_history.time.time", lambda: now)

    assert api.quicksale_refresh({})["ok"] is True
    c = db.conn()
    try:
        assert c.execute("SELECT COUNT(*) FROM listing_price_history_v1").fetchone()[0] == 1
    finally:
        c.close()

    monkeypatch.setattr(api, "_radar_scan_with_status", lambda *_a, **_kw:
                        ([{**row, "호가": 45_000, "fetched_at": now + 1}],
                         {"usable": False, "queryable_regions": 1, "successful_regions": []}))
    assert api.quicksale_refresh({})["ok"] is False
    c = db.conn()
    try:
        assert c.execute("SELECT COUNT(*) FROM listing_price_history_v1").fetchone()[0] == 1
    finally:
        c.close()
