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
