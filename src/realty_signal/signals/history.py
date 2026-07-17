"""시그널 스냅샷 저장/비교 — 주간 등급 변화 감지(알림용)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SNAPSHOT_FILE = Path("data/cache/snapshot.json")

# 등급 서열(상승/하락 방향 판정용)
_RANK = {"SELL_RISK": 0, "NEUTRAL": 1, "WATCH": 2, "BUY": 3, "STRONG_BUY": 4}


def load_snapshot(path: Path | None = None) -> dict:
    path = path or SNAPSHOT_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_snapshot(df: pd.DataFrame, as_of: str, path: Path | None = None) -> None:
    path = path or SNAPSHOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    snap = {"as_of": as_of, "signals": dict(zip(df["region"], df["signal"]))}
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")


def diff(prev: dict, df: pd.DataFrame) -> list[dict]:
    """이전 스냅샷 대비 등급이 바뀐 지역 목록(변화 큰 순)."""
    old = prev.get("signals", {})
    changes = []
    for _, r in df.iterrows():
        region, new = r["region"], r["signal"]
        was = old.get(region)
        if was and was != new:
            delta = _RANK.get(new, 1) - _RANK.get(was, 1)
            direction = "▲매수기회" if delta > 0 else "▼매도경고"
            if new == "SELL_RISK":
                direction = "▼매도경고"
            changes.append(
                {"region": region, "old": was, "new": new, "delta": delta,
                 "direction": direction, "근거": r.get("근거", "")}
            )
    changes.sort(key=lambda c: abs(c["delta"]), reverse=True)
    return changes
