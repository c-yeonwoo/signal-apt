"""Serve cached quotes immediately and renew due sources through the shared job lease."""

import os
import threading
import time

_lock = threading.Lock()
_running: set[str] = set()
_checked: dict[str, float] = {}


def schedule(kinds: set[str], *, private_allowed: bool) -> None:
    if not private_allowed or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    from realty_signal import api

    sources = {
        "급매": ("quicksale", api._quicksale_stale, api.quicksale_refresh),
        "찐매물": ("certified", api._certified_stale, api.certified_refresh),
        "일반매물": ("hanbang", api._hanbang_stale, api.hanbang_refresh),
    }
    for kind in kinds & sources.keys():
        name, due, refresh = sources[kind]
        _enqueue(name, due, lambda refresh=refresh: refresh({}), interval=3600)


def schedule_market() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    from realty_signal import api
    _enqueue("kb", api._kb_due_now, api._refresh_kb_if_due, interval=6 * 3600)


def _enqueue(name, due, refresh, *, interval):
    from realty_signal import jobs
    with _lock:
        now = time.monotonic()
        if name in _running or now - _checked.get(name, -float("inf")) < 60:
            return
        _checked[name] = now
        try:
            if not due():
                return
        except Exception:  # A health-check failure must not block the cached response.
            return
        _running.add(name)

    def run():
        try:
            # Same names as the scheduler: concurrent requests/workers share a lease
            # and a failed source keeps its one-hour retry clock.
            jobs.run(name, refresh, interval=interval, retry=3600, expedite=True)
        finally:
            with _lock:
                _running.discard(name)

    try:
        threading.Thread(target=run, name=f"source-refresh-{name}", daemon=True).start()
    except RuntimeError:
        with _lock:
            _running.discard(name)
