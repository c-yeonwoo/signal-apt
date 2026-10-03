import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from realty_signal import api, db, jobs


def test_only_one_worker_runs_job_and_due_time_survives():
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        assert release.wait(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(jobs.run, "source", work, interval=60)
        assert entered.wait(3)
        assert jobs.run("source", lambda: None, interval=60)["status"] == "not_due_or_running"
        release.set()
        assert first.result()["status"] == "complete"
    assert jobs.run("source", lambda: None, interval=60)["status"] == "not_due_or_running"
    assert jobs.status()[0]["last_success"] > 0


def test_failure_does_not_prevent_another_job():
    assert jobs.run("broken", lambda: {"ok": False}, interval=60)["status"] == "failed"
    assert jobs.run("healthy", lambda: None, interval=60)["status"] == "complete"
    by = {r["name"]: r for r in jobs.status()}
    assert by["broken"]["failures"] == 1
    assert by["healthy"]["failures"] == 0


def test_due_source_expedites_persisted_noop_interval():
    assert jobs.run("stale_kb", lambda: None, interval=6 * 3600)["status"] == "complete"
    assert jobs.run("stale_kb", lambda: None, interval=6 * 3600)["status"] == "not_due_or_running"
    assert jobs.run("stale_kb", lambda: None, interval=6 * 3600, expedite=True)["status"] == "complete"


def test_expedite_respects_failure_retry_clock():
    assert jobs.run("failed_kb", lambda: {"ok": False}, interval=6 * 3600)["status"] == "failed"
    assert jobs.run("failed_kb", lambda: None, interval=6 * 3600, expedite=True)["status"] == "not_due_or_running"


def test_scheduler_launches_sources_independently(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "run", lambda name, *a, **kw: calls.append(name))
    async def exercise():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(api._auto_refresh_loop(), timeout=.2)
    asyncio.run(exercise())
    assert set(calls) == {"kb", "quicksale", "certified", "hanbang", "localities",
                          "school_zones", "digest", "backup", "watch_alerts"}


def test_scheduler_expedites_unverified_hanbang_rescan(monkeypatch):
    from realty_signal import config
    calls = {}
    monkeypatch.setattr(config, "personal_listing_email", lambda: "owner@example.com")
    monkeypatch.setattr(api, "_hanbang_stale", lambda: True)
    monkeypatch.setattr(jobs, "run", lambda name, *a, **kw: calls.setdefault(name, kw))
    async def exercise():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(api._auto_refresh_loop(), timeout=.2)
    asyncio.run(exercise())
    assert calls["hanbang"]["expedite"] is True


def test_kb_refresh_checks_stale_observation_daily():
    day = 86400
    now = 100 * day
    assert not api._kb_refresh_due(now - day + 1, 12, now)
    assert api._kb_refresh_due(now - day, 12, now)
    assert not api._kb_refresh_due(now - day, 7.9, now)
    assert api._kb_refresh_due(now - 7 * day, 7.9, now)
    assert api._kb_refresh_due(now - day, None, now)


def test_kb_due_now_reflects_source_freshness(monkeypatch, tmp_path):
    from realty_signal import store
    cache = tmp_path / "kb.json"
    cache.touch()
    monkeypatch.setattr(store, "CACHE_FILE", cache)
    monkeypatch.setattr(api, "_data_age_days", lambda: 12)
    db.kv_set("last_kb_fetch", time.time() - 2 * 86400)
    assert api._kb_due_now()
    db.kv_set("last_kb_fetch", time.time())
    assert not api._kb_due_now()


def test_kb_refresh_records_unchanged_observation(monkeypatch, synthetic_market):
    from realty_signal import store
    monkeypatch.setattr(store, "fetch", lambda: synthetic_market)
    monkeypatch.setattr(api, "_snapshot_signals", lambda asof: [])
    first = api._do_refresh()
    assert first["last_date"] == str(synthetic_market.last_date.date())
    assert api.kb_fetch_health()["observation_check"]["changed"] is None
    api._do_refresh()
    assert api.kb_fetch_health()["observation_check"]["changed"] is False
