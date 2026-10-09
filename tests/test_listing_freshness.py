"""Daily renewal does not expire a successfully collected quote after one day."""

import json
import os
from types import SimpleNamespace

import pytest

from realty_signal import api, jobs
from realty_signal.services import buyer_decision, listing_refresh, property_analysis, quote_check
from realty_signal.time_kst import today_kst


@pytest.mark.parametrize("kind", ["일반매물", "급매", "찐매물"])
@pytest.mark.parametrize("age,expired", [(2, False), (7, False), (7.01, True)])
@pytest.mark.parametrize("renewal", ["success", "preserved", "failed"])
def test_daily_refresh_and_weekly_price_validity_are_independent(tmp_path, monkeypatch, kind, age, expired, renewal):
    now = 1_800_000_000
    monkeypatch.setattr(api.time, "time", lambda: now)
    stamp = now - age * 86400
    path = tmp_path / "listings.json"
    row = {"단지명": "테스트", "지역": "노원구", "시도": "서울", "지역코드": "11350",
           "호가": 50000, "전용면적": 59, "fetched_at": stamp, "hanbang_id": "1", "naver_id": "1"}
    if renewal == "preserved":
        row["stale"] = True  # Raw history marker: not observed again in this partial scan.
    path.write_text(json.dumps({"_scan_ver": 99, "listings": [row]}))
    os.utime(path, (stamp, stamp))
    for field in ("HANBANG_FILE", "QUICKSALE_FILE", "CERTIFIED_FILE"):
        monkeypatch.setattr(api, field, path)
    monkeypatch.setattr(api, "_radar_refresh_status", lambda _path: {"ok": renewal != "failed"})
    monkeypatch.setattr(api, "_radar_verified_rows", lambda *_args: [row])
    monkeypatch.setattr(api, "_hanbang_verified_rows", lambda: [row])
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api.md, "assessed_signal_labels", lambda _day: {})
    monkeypatch.setattr(api, "_listing_region_matches_kb", lambda *_args: True)
    monkeypatch.setattr(api, "_timing_asof", lambda: "2027-01-15")
    cached = api._radar_cached_response(path, 1)
    assert cached["refresh_due"] is True
    assert cached["stale"] is expired
    listing = api._build_listings({kind}, include_private=True)[0]
    assert listing["총액"] == 50000
    assert listing["stale"] is expired
    assert listing["refresh_due"] is True
    assert listing["refresh_failed"] is (renewal == "failed")
    assert not listing.get("degraded")
    assert listing["fetched_at"] == stamp
    assert row.get("stale", False) is (renewal == "preserved")
    assert property_analysis.snapshot(listing)["asking_manwon"] == 50000
    snapshot = property_analysis.snapshot(listing)
    detail = {"identity_status": "single_observed", "평형별": [{"전용㎡": 59,
              "비교거래": {"상태": "관측", "건수": 3, "기준일": today_kst().isoformat(),
                       "중앙값": 50000, "최저": 49000, "최고": 51000, "거래월범위": "최근 6개월"}}]}
    comparison = quote_check.assess_listing(detail, snapshot)
    assert comparison["상태"] == ("보류" if expired else "관측비교")
    assert comparison["호가차이율"] == (None if expired else 0)
    fit = property_analysis.buyer_fit(snapshot,
        {"매수력": {"최대매수가": 60000, "가정버전": 1}, "매수지역코드": "kb:1135000000"},
        confirmed_power=(60000, SimpleNamespace(region="노원구", sido="서울")))
    assert fit["status"] == ("unknown" if expired else "within")


def test_read_refresh_is_private_nonblocking_and_uses_scheduler_lease(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(listing_refresh, "_running", set())
    monkeypatch.setattr(listing_refresh, "_checked", {})
    monkeypatch.setattr(api, "_hanbang_stale", lambda: True)
    threads, calls = [], []
    monkeypatch.setattr(listing_refresh.threading, "Thread", lambda **kw:
                        SimpleNamespace(start=lambda: threads.append(kw["target"])))
    monkeypatch.setattr(jobs, "run", lambda name, fn, **kw: calls.append((name, kw)))
    listing_refresh.schedule({"일반매물"}, private_allowed=False)
    assert not threads
    listing_refresh.schedule({"일반매물"}, private_allowed=True)
    listing_refresh.schedule({"일반매물"}, private_allowed=True)
    assert len(threads) == 1 and not calls
    threads[0]()
    assert calls == [("hanbang", {"interval": 3600, "retry": 3600, "expedite": True})]
    assert not listing_refresh._running


def test_collected_asking_is_not_described_as_unknown_or_modeled():
    lines = buyer_decision.card_lines({"유형": "일반매물", "price_kind": "asking", "총액": 50000}, {})
    assert "수집된 매도 호가" in lines["price"]
    modeled = buyer_decision.card_lines({"유형": "단지", "가격출처": "단지평단추정", "추정가": 50000,
                                        "자금": {"필요현금": 30000}}, {})
    assert "참고가격" in modeled["price"] and "참고가격" in modeled["cash"]


def test_market_read_refresh_uses_same_kb_job_and_does_not_wait(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(listing_refresh, "_running", set())
    monkeypatch.setattr(listing_refresh, "_checked", {})
    monkeypatch.setattr(api, "_kb_due_now", lambda: True)
    threads, jobs_called, refreshed = [], [], []
    monkeypatch.setattr(api, "_refresh_kb_if_due", lambda: refreshed.append(True))
    monkeypatch.setattr(listing_refresh.threading, "Thread", lambda **kw:
                        SimpleNamespace(start=lambda: threads.append(kw["target"])))
    def run(name, fn, **kw):
        jobs_called.append((name, kw))
        fn()
    monkeypatch.setattr(jobs, "run", run)
    listing_refresh.schedule_market()
    listing_refresh.schedule_market()
    assert len(threads) == 1 and not refreshed
    threads[0]()
    assert refreshed == [True]
    assert jobs_called == [("kb", {"interval": 21600, "retry": 3600, "expedite": True})]
