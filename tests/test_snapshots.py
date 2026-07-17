"""스냅샷 통합 단위 테스트."""

from __future__ import annotations

import json

import pandas as pd

from realty_signal import db
from realty_signal.brain import snapshots
from realty_signal.signals import history


def test_snapshot_unified_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(history, "SNAPSHOT_FILE", tmp_path / "snapshot.json")
    db._migrated[0] = False

    df = pd.DataFrame([
        {"region": "강남구", "signal": "BUY", "근거": "test"},
        {"region": "마포구", "signal": "WATCH", "근거": "test"},
    ])
    signals = {"강남구": "BUY", "마포구": "WATCH"}
    snapshots.save(signals, "2026-07-14", df)

    loaded = snapshots.load()
    assert loaded["as_of"] == "2026-07-14"
    assert loaded["signals"]["강남구"] == "BUY"
    assert db.kv_get(snapshots.KV_KEY)["강남구"] == "BUY"
    assert json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))["signals"]["마포구"] == "WATCH"

    changes = snapshots.diff(loaded, pd.DataFrame([
        {"region": "강남구", "signal": "STRONG_BUY", "근거": "up"},
        {"region": "마포구", "signal": "WATCH", "근거": "same"},
    ]))
    assert len(changes) == 1
    assert changes[0]["region"] == "강남구"
