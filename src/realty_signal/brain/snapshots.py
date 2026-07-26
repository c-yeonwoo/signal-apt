"""시그널 스냅샷 통합 — CLI snapshot.json ↔ API db.kv signal_snapshot.

두 저장소 형식이 달라 diff/알림이 어긋날 수 있어, 읽기·쓰기를 한 경로로 모은다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from realty_signal import db
from realty_signal.signals import history

KV_KEY = "signal_snapshot"


def load() -> dict:
    """{as_of: str|None, signals: {region: signal}}."""
    file_snap = history.load_snapshot() if history.SNAPSHOT_FILE.exists() else {}
    kv = db.kv_get(KV_KEY)
    signals = dict(file_snap.get("signals") or {})
    if isinstance(kv, dict):
        for region, sig in kv.items():
            if region not in signals and isinstance(sig, str):
                signals[region] = sig
    as_of = file_snap.get("as_of")
    if not as_of and signals:
        as_of = db.kv_ts(KV_KEY)
        if as_of:
            import datetime as _dt
            as_of = _dt.date.fromtimestamp(as_of).isoformat()
    return {"as_of": as_of, "signals": signals}


def save(signals: dict[str, str], as_of: str, df: pd.DataFrame | None = None) -> None:
    """파일(JSON) + SQLite kv 동시 갱신."""
    if df is not None:
        history.save_snapshot(df, as_of)
    else:
        path = history.SNAPSHOT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"as_of": as_of, "signals": signals}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    db.kv_set(KV_KEY, signals)


def diff(prev: dict, df: pd.DataFrame) -> list[dict]:
    """history.diff 와 동일 — prev는 load() 결과 또는 history 형식."""
    if prev.get("signals"):
        return history.diff(prev, df)
    return history.diff({"signals": prev}, df)


CHANGE_LOG_KEY = "signal_changes"
CHANGE_LOG_MAX = 300


def log_changes(changes: list[dict], as_of: str) -> int:
    """변화를 알림 로그에 적재. 같은 (지역, 날짜)는 덮어쓰지 않고 건너뛴다."""
    if not changes:
        return 0
    old = db.kv_get(CHANGE_LOG_KEY) or []
    seen = {(c.get("region"), c.get("date")) for c in old}
    fresh = [{"region": c["region"], "from": c.get("from") or c.get("old"),
              "to": c.get("to") or c.get("new"), "date": as_of}
             for c in changes if (c["region"], as_of) not in seen]
    if fresh:
        db.kv_set(CHANGE_LOG_KEY, (fresh + old)[:CHANGE_LOG_MAX])
    return len(fresh)


def advance(cur: dict[str, str], as_of: str, df: pd.DataFrame | None = None) -> list[dict]:
    """스냅샷을 앞으로 당기면서 변화를 로그에 남긴다.

    스냅샷을 옮기는 곳이 둘(서버 `_do_refresh`, CLI `signal watch`)인데 로그를 쓰는 쪽은
    서버뿐이었다. 토요일 CLI 가 먼저 돌면 서버는 prev == cur 을 보고 "변화 없음"을 남겼고,
    실제로 2026-07-13·07-20 두 주가 통째로 비었다. **스냅샷을 당기는 곳은 전부 이 함수를 쓴다.**
    """
    prev = load().get("signals") or {}
    changes = []
    if prev:      # 최초 1회는 스냅샷만 저장(가짜 변동 방지)
        changes = [{"region": r, "from": prev[r], "to": s, "date": as_of}
                   for r, s in cur.items() if prev.get(r) and prev[r] != s]
    log_changes(changes, as_of)
    save(cur, as_of, df)
    return changes
