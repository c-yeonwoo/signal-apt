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
    from realty_signal import api, jobs

    sources = {
        "급매": ("quicksale", api._quicksale_stale, api.quicksale_refresh),
        "찐매물": ("certified", api._certified_stale, api.certified_refresh),
        "일반매물": ("hanbang", api._hanbang_stale, api.hanbang_refresh),
    }
    for kind in kinds & sources.keys():
        name, due, refresh = sources[kind]
        with _lock:
            now = time.monotonic()
            if name in _running or now - _checked.get(name, -float("inf")) < 60:
                continue
            _checked[name] = now
            if not due():
                continue
            _running.add(name)

        def run(name=name, refresh=refresh):
            try:
                # Same names as the scheduler: concurrent requests/workers share a lease
                # and a failed source keeps its one-hour retry clock.
                jobs.run(name, lambda: refresh({}), interval=3600, retry=3600, expedite=True)
            finally:
                with _lock:
                    _running.discard(name)

        try:
            threading.Thread(target=run, name=f"listing-refresh-{name}", daemon=True).start()
        except RuntimeError:
            with _lock:
                _running.discard(name)
