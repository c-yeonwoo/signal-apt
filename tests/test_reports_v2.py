"""Report endpoints expose deterministic evidence without opening private sources."""

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from realty_signal.routes import reports_v2
from realty_signal.services import market_data as md
from realty_signal.signals.engine import SignalConfig

from test_signal_assessment_v2 import _kb, _row
from test_discovery_v2 import _row as listing_row
from test_discovery_finance import _profile as finance_profile


def test_region_report_exposes_failed_conditions_and_cautions(monkeypatch):
    monkeypatch.setattr(md, "kb", lambda: _kb((-0.15,) * 4))
    monkeypatch.setattr(md, "signals_df", lambda: pd.DataFrame([_row("BUY")]))
    monkeypatch.setattr(md, "signal_config", SignalConfig)
    from realty_signal.services import signal_assessment as sa
    original = sa.build
    monkeypatch.setattr(sa, "build", lambda kb, row, config: original(
        kb, row, config, asof=date(2026, 9, 28), today=date(2026, 10, 3)))
    report = json.loads(reports_v2.region_report("중구").body)
    assert report["assessment"]["raw_grade"] == "BUY"
    assert report["assessment"]["display_grade"] == "판단 보류"
    assert "price_direction_conflict" in report["unknowns"]
    assert any(reason["reason_id"] == "sale_momentum" for reason in report["cautions"])
    by_code = json.loads(reports_v2.region_report("kb:1114000000").body)
    assert by_code["subject"] == {"region": "중구", "region_id": "kb:1114000000"}


def test_region_code_route_rejects_unverified_legacy_cache(monkeypatch):
    monkeypatch.setattr(md, "kb", lambda: _kb(identity_verified=False))
    with pytest.raises(HTTPException) as error:
        reports_v2.region_report("kb:1114000000")
    assert error.value.status_code == 404


def test_discovery_region_choices_require_verified_current_unique_codes(monkeypatch):
    source = SimpleNamespace(identity_verified=True,
        codes={"중구": "1114000000", "부산 중구": "2611000000",
               "인천 중구": "2811000000", "이름만": "", "겹침A": "1115000000", "겹침B": "1115000000"},
        regions={"중구": None, "부산 중구": None, "인천 중구": None,
                 "이름만": None, "겹침A": None, "겹침B": None})
    monkeypatch.setattr(md, "kb", lambda: source)
    options = json.loads(reports_v2.discovery_regions().body)["regions"]
    assert {option["code"] for option in options} == {"11140", "26110"}
    assert reports_v2._canonical_discovery_region({"region_code": "11140"}) == {"region_code": "11140"}
    with pytest.raises(HTTPException, match="지역 이름만으로는"):
        reports_v2._canonical_discovery_region({"region": "중구"})
    with pytest.raises(HTTPException, match="현재 지역 코드를"):
        reports_v2._canonical_discovery_region({"region_code": "28110"})
    source.identity_verified = False
    assert json.loads(reports_v2.discovery_regions().body)["status"] == "unverified"
    with pytest.raises(HTTPException):
        reports_v2._canonical_discovery_region({"region_code": "11140"})


def test_discovery_route_passes_verified_region_hint_for_uncoded_source_rows(monkeypatch):
    from realty_signal.services import discovery_v2

    monkeypatch.setattr(reports_v2, "_discovery_regions", lambda: [
        {"code": "11140", "name": "중구", "sido": "서울", "label": "서울 · 중구"}])
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: False)
    captured = {}
    original = discovery_v2.discover

    def record(rows, spec, **kwargs):
        captured.update(kwargs)
        return original(rows, spec, **kwargs)

    monkeypatch.setattr(discovery_v2, "discover", record)
    reports_v2.discovery(None, {"region_code": "11140"})
    assert captured["region_hint"] == {"name": "중구", "sido": "서울"}


def test_listing_report_keeps_price_evidence_when_buyer_profile_fails(monkeypatch):
    from realty_signal import api, db
    from realty_signal.services import property_analysis
    from realty_signal.routes import market

    row = listing_row("profile-failure", price=50_000, area=70)
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(property_analysis, "resolve", lambda key, private_allowed: row)
    monkeypatch.setattr(api, "complex_detail", lambda region, name, **kwargs: {"평형별": []})
    monkeypatch.setattr(api, "_buyer_params", lambda profile: (_ for _ in ()).throw(
        AssertionError("must not estimate finance from missing profile")))
    monkeypatch.setattr(db, "profile_get", lambda uid: (_ for _ in ()).throw(OSError("profile unavailable")))
    monkeypatch.setattr(db, "kv_get", lambda *args, **kwargs: [])

    base = json.loads(market.listing_analysis(None, row["key"], stage="base").body)
    assert base["buyer_fit"]["status"] == "unknown"
    assert base["partial_failures"] == ["buyer_profile_unavailable"]
    report = json.loads(reports_v2.listing_report(None, row["key"]).body)
    assert report["subject"]["key"] == row["key"]
    assert report["price"]["상태"] == "보류"  # 거래 자료는 별도 미확인, 리포트 자체는 유지
    assert report["buyer_fit"]["status"] == "unknown"
    assert report["partial_failures"] == ["buyer_profile_unavailable"]
    assert report["lines"] is None


def test_listing_report_does_not_reintroduce_affordable_line_for_stale_budget(monkeypatch):
    from realty_signal import api, db
    from realty_signal.services import property_analysis

    row = listing_row("stale-budget", price=50_000, area=70)
    row["자금"] = {"가능": True, "필요현금": 20_000, "월상환": 100}
    row["예산내"] = True
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(property_analysis, "resolve", lambda key, private_allowed: row)
    monkeypatch.setattr(api, "complex_detail", lambda region, name, **kwargs: {"평형별": []})
    monkeypatch.setattr(db, "profile_get", lambda uid: {
        "가용자본": 50_000, "매수력": {"최대매수가": 100_000}})
    monkeypatch.setattr(db, "kv_get", lambda *args, **kwargs: [])
    report = json.loads(reports_v2.listing_report(None, row["key"]).body)
    assert report["buyer_fit"]["status"] == "unknown"
    assert "검증 가능한 확정 매수력이 없어" in report["lines"]["cash"]
    assert "계산상 됩니다" not in report["lines"]["cash"]
    assert report["decision"]["feasibility"] == "unknown"


def test_listing_explanation_keeps_id_for_same_evidence_but_rejects_changed_price(monkeypatch):
    from realty_signal import api, db
    from realty_signal.services import buyer_decision, property_analysis, report_narrative

    row = listing_row("stable-explanation", price=50_000, area=70)
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(property_analysis, "resolve", lambda key, private_allowed: row)
    monkeypatch.setattr(api, "complex_detail", lambda region, name, **kwargs: {"평형별": []})
    monkeypatch.setattr(db, "profile_get", lambda uid: {})
    monkeypatch.setattr(db, "kv_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(reports_v2.config, "rollout_flags", lambda: {
        "report_v2_enabled": True, "contextual_explanations_enabled": True})
    start = datetime(2026, 10, 5, tzinfo=timezone.utc)
    ticks = iter(start + timedelta(seconds=n) for n in range(4))
    monkeypatch.setattr(buyer_decision, "datetime", SimpleNamespace(now=lambda tz: next(ticks)))
    monkeypatch.setattr(report_narrative, "enqueue", lambda *args, **kwargs: (
        {"job_id": "synthetic", "status": "pending"}, 202))

    first = json.loads(reports_v2.listing_report(None, row["key"]).body)
    second = json.loads(reports_v2.listing_report(None, row["key"]).body)
    assert first["decision"]["generated_at"] != second["decision"]["generated_at"]
    assert first["report_id"] == second["report_id"]
    accepted = reports_v2.explanation_request(None, first["report_id"], {
        "type": "listing", "key": row["key"], "mode": "easy"})
    assert accepted.status_code == 202

    row["총액"] = 51_000
    changed = json.loads(reports_v2.listing_report(None, row["key"]).body)
    assert changed["report_id"] != first["report_id"]


def test_listing_report_get_uses_cache_and_post_requests_refresh(monkeypatch):
    from realty_signal import api
    from realty_signal.services import property_analysis

    row = listing_row("selective-trade", price=50_000, area=70)
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(property_analysis, "resolve", lambda key, private_allowed: row)
    calls = []
    def detail(region, name, *, cache_only=False):
        calls.append(cache_only)
        return {"status": "unavailable" if cache_only else "ready", "평형별": []}
    monkeypatch.setattr(api, "complex_detail", detail)
    monkeypatch.setattr(api, "_buyer_params", lambda profile: (_ for _ in ()).throw(ValueError()))
    first = json.loads(reports_v2.listing_report(None, row["key"]).body)
    assert calls == [True]
    assert first["complex"]["status"] == "unavailable"
    assert "trade_cache_unavailable" in first["partial_failures"]
    assert first["status"] == "partial"
    refreshed = json.loads(reports_v2.listing_report_enrich(None, {"key": row["key"]}).body)
    assert calls == [True, False]
    assert "trade_cache_unavailable" not in refreshed["partial_failures"]


def test_failed_trade_enrichment_remains_partial(monkeypatch):
    from realty_signal import api
    from realty_signal.services import property_analysis
    row = listing_row("trade-fail", price=50_000, area=70)
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(property_analysis, "resolve", lambda key, private_allowed: row)
    monkeypatch.setattr(api, "complex_detail", lambda region, name: {"status": "failed", "평형별": []})
    monkeypatch.setattr(api, "_buyer_params", lambda profile: (_ for _ in ()).throw(ValueError()))
    report = json.loads(reports_v2.listing_report_enrich(None, {"key": row["key"]}).body)
    assert report["status"] == "partial"
    assert "trade_source_unavailable" in report["partial_failures"]


def test_cache_only_complex_detail_does_not_open_trade_or_gongsi_source(monkeypatch):
    from realty_signal import api
    from realty_signal.ingest import complex as cx
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api, "_display_signal_map", lambda: {})
    monkeypatch.setattr(api, "_code_of", lambda region: "11140")
    monkeypatch.setattr(cx, "fetch_complex", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("GET must not fetch trades")))
    monkeypatch.setattr(api, "_gongsi_for", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("GET must not fetch gongsi")))
    result = api.complex_detail("중구", "테스트단지", cache_only=True)
    assert result["status"] == "unavailable"


def test_private_discovery_does_not_open_source_without_personal_access(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: False)
    monkeypatch.setattr(api, "_build_listings", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("private source accessed")))
    monkeypatch.setattr(api, "hanbang", lambda: (_ for _ in ()).throw(AssertionError("private cache accessed")))
    monkeypatch.setattr(reports_v2.deps, "uid", lambda _request: (_ for _ in ()).throw(
        AssertionError("private profile accessed")))
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000,
                                                    "max_monthly_manwon": 200}).body)
    assert result["private_access"] is False
    assert result["source_state"] == "forbidden"
    assert result["counts"]["matched"] == 0


def test_discovery_reports_partial_coverage_without_hiding_good_source(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "partial", "regions": ["노원구"],
                                                  "refresh": {"failed_requests": 2, "limited_regions": ["노원구"]}})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "failed", "regions": []})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty", "regions": ["노원구"]})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("one", price=50000, area=70)] if "일반매물" in kinds else [])
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert result["source_state"] == "partial"
    assert result["coverage"]["regions"] == ["노원구"]
    assert result["sources"][0]["failed_requests"] == 2
    assert result["sources"][1]["state"] == "failed"
    assert len(result["groups"]["matched"]) == 1


def test_discovery_marks_failed_refresh_cache_as_verify(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "stale_failed", "regions": ["노원구"]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "never_scanned"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "never_scanned"})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("old", price=50000, area=70)] if "일반매물" in kinds else [])
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert not result["groups"]["matched"]
    assert result["groups"]["verify"][0]["constraints"][0]["status"] == "unknown"


def test_discovery_recovers_other_source_when_combined_read_fails(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "regions": ["노원구"]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "ready", "regions": ["노원구"]})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})

    def load(kinds, **_):
        if "급매" in kinds:
            raise ValueError("damaged source")
        return [listing_row("one", price=50000, area=70)] if "일반매물" in kinds else []

    monkeypatch.setattr(api, "_build_listings", load)
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert result["source_state"] == "partial"
    assert result["sources"][1]["state"] == "failed"
    assert len(result["groups"]["matched"]) == 1


def test_discovery_does_not_call_missing_rows_verified_empty(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "listings": [{"id": 1}]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "_build_listings", lambda *_a, **_k: [])
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert result["sources"][0]["state"] == "failed"
    assert result["source_state"] == "partial_empty"


def test_discovery_finance_context_keeps_policy_unknown_and_profile_private(monkeypatch):
    from realty_signal import api, db
    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(
        codes={"노원구": "1135000000"}, regions=["노원구"], identity_verified=True))
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(reports_v2.deps, "uid", lambda _request: 7)
    profile = finance_profile()
    monkeypatch.setattr(db, "profile_get", lambda _uid: profile)
    monkeypatch.setattr(api, "_sido_of", lambda _region: "서울")
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "regions": ["노원구"]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("one", price=50_000, area=70)] if "일반매물" in kinds else [])
    response = reports_v2.discovery(None, {"max_monthly_manwon": 200})
    result = json.loads(response.body)
    assert response.headers["cache-control"] == "private, no-store"
    assert result["finance_context"]["policy_status"] == "unverified"
    assert result["groups"]["verify"][0]["finance"]["status"] == "policy_unverified"
    assert "가용자본" not in response.body.decode()


def test_discovery_finance_profile_failure_does_not_hide_listings(monkeypatch):
    from realty_signal import api, db
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(reports_v2.deps, "uid", lambda _request: 7)
    monkeypatch.setattr(db, "profile_get", lambda _uid: (_ for _ in ()).throw(OSError("db down")))
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready"})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("one", price=50_000, area=70)] if "일반매물" in kinds else [])
    result = json.loads(reports_v2.discovery(None, {"max_monthly_manwon": 200}).body)
    assert result["finance_context"]["status"] == "profile_unavailable"
    assert result["groups"]["verify"][0]["finance"]["status"] == "profile_unavailable"


def test_discovery_cursor_restarts_when_finance_profile_changes(monkeypatch):
    from realty_signal import api, db
    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(
        codes={"노원구": "1135000000"}, regions=["노원구"], identity_verified=True))
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(reports_v2.deps, "uid", lambda _request: 7)
    profile = finance_profile()
    monkeypatch.setattr(db, "profile_get", lambda _uid: profile)
    monkeypatch.setattr(api, "_sido_of", lambda _region: "서울")
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready"})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("one", price=50_000, area=70), listing_row("two", price=51_000, area=70)]
        if "일반매물" in kinds else [])
    first = json.loads(reports_v2.discovery(None, {"max_monthly_manwon": 200, "limit": 1}).body)
    profile["가용자본"] = 90_000
    with pytest.raises(HTTPException) as error:
        reports_v2.discovery(None, {"max_monthly_manwon": 200, "limit": 1,
                                    "cursor": first["next_cursor"]})
    assert error.value.status_code == 409


def test_discovery_returns_conflict_when_page_source_changes(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "regions": ["노원구"]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    rows = [listing_row("one", price=50000), listing_row("two", price=51000)]
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: rows if "일반매물" in kinds else [])
    first = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1}).body)
    rows[0] = listing_row("one", price=49000)
    with pytest.raises(HTTPException) as error:
        reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1,
                                    "cursor": first["next_cursor"]})
    assert error.value.status_code == 409


def test_discovery_cursor_restarts_when_source_health_changes(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    health = {"state": "ready", "regions": ["노원구"]}
    monkeypatch.setattr(api, "hanbang", lambda: health)
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("one", price=50000), listing_row("two", price=51000)]
        if "일반매물" in kinds else [])
    first = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1}).body)
    health["state"] = "partial"
    with pytest.raises(HTTPException) as error:
        reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1,
                                    "cursor": first["next_cursor"]})
    assert error.value.status_code == 409
