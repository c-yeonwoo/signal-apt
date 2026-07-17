"""Alert Accountability (Phase 5) — 과거 signal_changes 알림을 실제 KB 가격으로 채점.

ML 없이 피드백 루프를 닫는다: 알림이 나간 시점(from→to) 대비 이후 N주(기본 12주)
KB 매매가격지수(`signals.engine.price_index_from`)가 방향대로 움직였는지만 집계한다.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd

from realty_signal import db
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.signals.engine import price_index_from

HORIZON_WEEKS = 12

# 시그널 등급 랭크 — brain/alerts.py 의 _SIG_RANK 와 동일 기준 (승급/강등 방향 판정용)
_RANK = {"SELL_RISK": 0, "NEUTRAL": 1, "WATCH": 2, "BUY": 3, "STRONG_BUY": 4}

FEEDBACK_EVENT = "alert_feedback"
FEEDBACK_KINDS = frozenset({"signal_upgrade", "high_timing", "nbhd_change"})


def _direction(frm: str | None, to: str | None) -> str | None:
    """from→to 등급 변화 방향. 랭크 비교 불가하면 None."""
    rf, rt = _RANK.get(frm), _RANK.get(to)
    if rf is None or rt is None or rf == rt:
        return None
    return "up" if rt > rf else "down"


def score_change(kb: KBWeekly, change: dict, horizon_weeks: int = HORIZON_WEEKS) -> dict | None:
    """알림 1건(signal_changes 엔트리)을 이후 N주 가격 변화로 채점.

    backtest_summary 와 동일하게 "발생일 이후 N번째 관측치"라는 위치 기반 방식을
    쓴다(달력 주 수가 아니라 실제 데이터 행 수 기준 — 결측주 보정).
    데이터가 아직 N주치 안 쌓였으면 pending, 방향(up/down) 판정이 안 되면 None.
    """
    region = change.get("region")
    date_s = change.get("date")
    direction = _direction(change.get("from"), change.get("to"))
    if not region or not date_s or direction is None:
        return None
    try:
        asof = pd.Timestamp(date_s)
    except Exception:  # noqa: BLE001
        return None

    sale = kb.series(region, "sale_change")
    if sale.empty:
        return None
    idx = price_index_from(sale)
    i0 = idx.asof(asof)
    if pd.isna(i0) or not i0:
        return None

    out: dict[str, Any] = {
        "region": region, "from": change.get("from"), "to": change.get("to"),
        "date": date_s, "direction": direction, "horizon_weeks": horizon_weeks,
    }
    after = idx[idx.index > asof]
    if len(after) < horizon_weeks:
        out["pending"] = True
        return out

    a = after.iloc[horizon_weeks - 1]
    if pd.isna(a) or not i0:
        out["pending"] = True
        return out

    pct = round((a / i0 - 1) * 100, 2)
    up = pct > 0
    hit = up if direction == "up" else not up
    out.update({"pending": False, "pct": pct, "hit": bool(hit)})
    return out


def track_record(
    kb: KBWeekly, changes: list[dict], horizon_weeks: int = HORIZON_WEEKS, limit: int = 200,
) -> dict:
    """signal_changes 알림 로그 전체(최신순)를 채점해 적중률 집계 + 최근 목록 반환."""
    scored: list[dict] = []
    pending = 0
    skipped = 0
    for change in (changes or [])[:limit]:
        r = score_change(kb, change, horizon_weeks)
        if r is None:
            skipped += 1
            continue
        if r.get("pending"):
            pending += 1
            continue
        scored.append(r)

    def _rate(rows: list[dict]) -> float | None:
        return round(sum(1 for r in rows if r["hit"]) / len(rows) * 100, 1) if rows else None

    up_rows = [r for r in scored if r["direction"] == "up"]
    down_rows = [r for r in scored if r["direction"] == "down"]

    return {
        "horizon_weeks": horizon_weeks,
        "scored": len(scored),
        "pending": pending,
        "skipped": skipped,
        "hit_rate": _rate(scored),
        "hit_rate_up": _rate(up_rows),
        "hit_rate_down": _rate(down_rows),
        "recent": scored,
    }


def feedback_summary(days: int = 90) -> dict:
    """alert_feedback 이벤트(👍/👎) 를 kind 별로 집계."""
    since = int(time.time()) - max(1, days) * 86400
    c = db.conn()
    rows = c.execute(
        "SELECT props FROM events WHERE name=? AND ts>=?",
        (FEEDBACK_EVENT, since),
    ).fetchall()
    c.close()

    by_kind: dict[str, dict[str, int]] = {}
    total = 0
    for (props_s,) in rows:
        try:
            p = json.loads(props_s) if props_s else {}
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(p, dict):
            continue
        kind = p.get("kind") or "unknown"
        useful = bool(p.get("useful"))
        agg = by_kind.setdefault(kind, {"useful": 0, "not_useful": 0})
        agg["useful" if useful else "not_useful"] += 1
        total += 1

    return {"days": days, "total": total, "by_kind": by_kind}
