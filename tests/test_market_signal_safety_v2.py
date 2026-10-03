"""The primary market API must not leak an unsafe raw grade as a display label."""

import json
from datetime import date
from types import SimpleNamespace

import pandas as pd

from realty_signal.routes import market
from realty_signal.services import market_data as md
from realty_signal.signals.engine import SignalConfig
from realty_signal import api

from test_signal_assessment_v2 import _kb, _row


def test_primary_signal_api_keeps_raw_for_audit_but_displays_held(monkeypatch):
    row = _row("BUY")
    frame = pd.DataFrame([row])
    monkeypatch.setattr(md, "kb", lambda: _kb((-0.15,) * 4))
    monkeypatch.setattr(md, "signals_df", lambda: frame)
    monkeypatch.setattr(md, "signal_config", SignalConfig)
    md.assessed_signal_labels.cache_clear()
    labels = md.assessed_signal_labels(date(2026, 10, 3).isoformat())
    assert labels["중구"]["display_signal"] == "HELD"
    result = market.signals()
    assert result[0]["signal"] == "BUY"
    assert result[0]["display_signal"] == "HELD"
    assert result[0]["assessment_status"] == "held"
    assert market.signals(only="BUY") == []
    assert market.signals(only="HELD")[0]["signal"] == "BUY"


def test_signal_api_assessment_failure_still_returns_held_rows(monkeypatch):
    monkeypatch.setattr(md, "kb", lambda: _kb((-0.15,) * 4))
    monkeypatch.setattr(md, "signals_df", lambda: pd.DataFrame([_row("STRONG_BUY")]))
    def unavailable(today):
        raise RuntimeError("assessment unavailable")
    monkeypatch.setattr(md, "assessed_signal_labels", unavailable)
    result = market.signals()
    assert result[0]["signal"] == "STRONG_BUY"
    assert result[0]["display_signal"] == "HELD"
    assert market.signals(only="STRONG_BUY") == []


def test_region_timing_never_resurrects_raw_buy_when_assessment_is_held(monkeypatch):
    monkeypatch.setattr(api, "signals", lambda: [
        {"region": "보류구", "signal": "STRONG_BUY", "display_signal": "HELD",
         "assessment_status": "held"}])
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-03")
    held = api._region_timing_row("보류구")
    assert held["signal"] == "HELD"
    assert held["타이밍점수"] is None and held["기회도"] is None
    assert held["assessment_status"] == "held"
    assert api._region_timing_row("구")["error"]


def test_region_timing_uses_only_ready_display_signal(monkeypatch):
    from realty_signal.ingest import pipeline

    monkeypatch.setattr(api, "signals", lambda: [
        {"region": "확인구", "signal": "STRONG_BUY", "display_signal": "BUY",
         "assessment_status": "ready"}])
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-03")
    monkeypatch.setattr(api, "_backtest", lambda: {"by_signal": []})
    monkeypatch.setattr(pipeline, "load_market_strength", lambda: {"regions": {
        "확인구": {"시장강도": 55, "시장강도라벨": "활발"}}})
    ready = api._region_timing_row("확인구")
    assert ready["signal"] == "BUY" and ready["assessment_status"] == "ready"
    assert "지역시그널 BUY" in ready["타이밍근거"]
    assert "STRONG_BUY" not in ready["타이밍근거"]
    assert ready["타이밍점수"] == 72
    assert ready["시장강도"] == 55
    assert "미합산" in ready["시장강도설명"]


def test_neighborhood_hides_raw_buy_explanation_when_held(monkeypatch):
    from realty_signal import personal_layer as pl
    from realty_signal.ingest import pipeline

    monkeypatch.setattr(api, "signals", lambda: [
        {"region": "보류구", "signal": "STRONG_BUY", "display_signal": "HELD",
         "assessment_status": "held", "해설": "지금 강력 매수", "근거": "원시 매수 근거"}])
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api.store, "load_localities", lambda: pd.DataFrame())
    monkeypatch.setattr(api, "_presale", lambda: [])
    monkeypatch.setattr(api, "db_has_redev_cache", lambda _region: False)
    monkeypatch.setattr(api.db, "policy_search", lambda *_a, **_k: [])
    monkeypatch.setattr(api, "_region_centroid", lambda *_a: None)
    monkeypatch.setattr(api, "_code_of", lambda _region: None)
    monkeypatch.setattr(api.db, "kv_get", lambda *_a, **_k: None)
    monkeypatch.setattr(api.config, "load_env", lambda: None)
    monkeypatch.setattr(api.config, "odsay_analysis_approved", lambda: False)
    monkeypatch.setattr(api, "_kb", lambda: SimpleNamespace(last_date=pd.Timestamp("2026-10-03")))
    monkeypatch.setattr(api, "_nbhd_week", lambda: "2026-W40")
    monkeypatch.setattr(api, "_regulation_of", lambda _region: "미확인")
    monkeypatch.setattr(api, "_uid", lambda _request: None)
    monkeypatch.setattr(api, "_personal_listings_allowed", lambda **_kwargs: False)
    monkeypatch.setattr(pl, "volume_summary", lambda _region: {})
    monkeypatch.setattr(pl, "macro_latest", lambda: {})
    monkeypatch.setattr(pl, "locality_bits", lambda _data: {})
    monkeypatch.setattr(pl, "ext_links", lambda _region: [])
    monkeypatch.setattr(pipeline, "region_entity", lambda *_a, **_k: SimpleNamespace(
        market_strength=None, market_strength_label=None, quicksale_count=None, provenance=None))

    out = api.neighborhood(None, "보류구")
    assert out["시그널"] == "HELD" and out["판정상태"] == "held"
    assert "지금 강력 매수" not in json.dumps(out, ensure_ascii=False)
    assert "원시 매수 근거" not in json.dumps(out, ensure_ascii=False)
    assert api.neighborhood(None, "구")["reason"] == "no_region"


def test_default_redevelopment_warm_uses_only_ready_buy_regions(monkeypatch):
    seen = []
    monkeypatch.setattr(api, "_display_signal_map", lambda: {"보류구": "HELD", "확인구": "BUY"})
    monkeypatch.setattr(api, "_redev_zones", lambda: None)
    monkeypatch.setattr(api.db, "kv_get", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "_redev_candidates", lambda region: seen.append(region))
    result = api.redev_warm({})
    assert seen == ["확인구"] and result["warmed"] == ["확인구"]


def test_listing_cards_and_timing_use_guarded_region_signal(monkeypatch):
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-03")
    monkeypatch.setattr(api, "_presale", lambda: [
        {"단지명": "보류 단지", "지역": "보류구", "시그널": "BUY", "상태": "접수예정", "관리번호": "1"},
        {"단지명": "확인 단지", "지역": "확인구", "시그널": "BUY", "상태": "접수예정", "관리번호": "2"},
    ])
    monkeypatch.setattr(md, "assessed_signal_labels", lambda today: {
        "보류구": {"display_signal": "HELD", "assessment_status": "held"},
        "확인구": {"display_signal": "BUY", "assessment_status": "ready"},
    })

    rows = {row["지역"]: row for row in api._build_listings({"청약"})}
    assert rows["보류구"]["원시시그널"] == "BUY"
    assert rows["보류구"]["시그널"] == "HELD"
    assert rows["확인구"]["시그널"] == "BUY"
    assert rows["확인구"]["기회도"] > rows["보류구"]["기회도"]


def test_listing_signal_assessment_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-03")
    monkeypatch.setattr(api, "_presale", lambda: [
        {"단지명": "미확인 단지", "지역": "미확인구", "시그널": "STRONG_BUY", "상태": "접수예정", "관리번호": "3"},
    ])
    def unavailable(today):
        raise RuntimeError("synthetic source failure")
    monkeypatch.setattr(md, "assessed_signal_labels", unavailable)

    row = api._build_listings({"청약"})[0]
    assert row["원시시그널"] == "STRONG_BUY"
    assert row["시그널"] == "HELD"
    assert row["판정상태"] == "held"


def test_public_listing_response_drops_audit_only_raw_grade(monkeypatch):
    from realty_signal.brain import ranking

    internal = [{"유형": "청약", "단지명": "보류 단지", "지역": "보류구",
                 "시그널": "HELD", "원시시그널": "STRONG_BUY", "기회도": None,
                 "key": "청약:1"}]
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: internal)
    monkeypatch.setattr(api, "_attach_card_lines", lambda rows, uid: rows)
    monkeypatch.setattr(api, "_uid", lambda request: None)
    monkeypatch.setattr(api, "_personal_listings_allowed", lambda **_kwargs: False)
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-04")
    monkeypatch.setattr(api, "_data_age_days", lambda: 1)
    monkeypatch.setattr(ranking, "engagement_scores", lambda **_kwargs: {})
    result = api.listings_all(None, "청약")
    assert result["listings"][0]["시그널"] == "HELD"
    assert "원시시그널" not in result["listings"][0]
    assert internal[0]["원시시그널"] == "STRONG_BUY"


def test_integrated_listing_does_not_borrow_same_named_other_province_signal(monkeypatch, tmp_path):
    cache = tmp_path / "quicksale.json"
    cache.write_text(json.dumps({"_scan_ver": api._QUICKSALE_SCAN_VER,
                                 "listings": [{"naver_id": "1", "단지명": "동명단지", "지역": "중구",
                                               "시도": "인천", "호가": 50000, "평형": 25,
                                               "시그널": "STRONG_BUY", "급매갭": -5}]}), encoding="utf-8")
    monkeypatch.setattr(api, "QUICKSALE_FILE", cache)
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api, "_sido_of", lambda *_: "서울")
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-03")
    monkeypatch.setattr(md, "assessed_signal_labels", lambda today: {
        "중구": {"display_signal": "STRONG_BUY", "assessment_status": "ready"}})
    row = api._build_listings({"급매"}, include_private=True)[0]
    assert row["원시시그널"] == "STRONG_BUY"
    assert row["시도"] == "인천" and row["시그널"] == "HELD"
