import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

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


def test_scheduler_launches_sources_independently(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "run", lambda name, *a, **kw: calls.append(name))
    async def exercise():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(api._auto_refresh_loop(), timeout=.2)
    asyncio.run(exercise())
    assert set(calls) == {"kb", "quicksale", "certified", "localities", "digest", "backup"}
