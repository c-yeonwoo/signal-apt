"""Read-only audit of assessments actually issued before their outcomes were known.

This is not a forecast probability or a causal estimate. Grades are immutable
issuance records; price outcomes use the currently available KB series, whose
historical values may have been revised since issuance.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.services.signal_assessment import INCHEON_RETIRED_CODES
from realty_signal.signals.engine import price_index_from
from realty_signal.time_kst import KST

HORIZON_DAYS = 84
OBSERVATION_WINDOW_DAYS = 6
DIRECTION = {"STRONG_BUY": "up", "BUY": "up", "SELL_RISK": "down"}


def _first_outcome(index: pd.Series, asof: date, target: date) -> tuple[date, float] | None:
    after = index[index.index >= pd.Timestamp(target)]
    if after.empty or (after.index[0].date() - target).days > OBSERVATION_WINDOW_DAYS:
        return None
    window = index[(index.index >= pd.Timestamp(asof)) & (index.index <= after.index[0])]
    if not window.index.is_unique or len(window) < 2 or any(
            (right - left).days > 8 for left, right in zip(window.index, window.index[1:])):
        return None
    return after.index[0].date(), float(after.iloc[0])


def audit_records(kb: KBWeekly, records) -> dict:
    """Evaluate chronological issuance rows, taking only the first per region/week.

    `records` must be ordered by issued_at then id, as the read-only query below
    does. A later same-week correction must not silently replace what was first
    shown to users.
    """
    counts: Counter = Counter()
    seen: set[tuple[str, str]] = set()
    cohorts: dict[str, dict] = {}
    indices: dict[str, pd.Series] = {}
    national = price_index_from(kb.series("전국", "sale_change"))
    kb_day = kb.last_date.date()

    def index_for(region: str) -> pd.Series:
        if region not in indices:
            indices[region] = price_index_from(kb.series(region, "sale_change"))
        return indices[region]

    for rec in records:
        counts["issued_rows"] += 1
        region_id, asof_s = str(rec["region_id"]), str(rec["asof"])
        key = (region_id, asof_s)
        if key in seen:
            counts["later_same_week_revisions"] += 1
            continue
        seen.add(key)
        counts["first_issued_region_weeks"] += 1
        try:
            item = json.loads(rec["data"])
            asof = date.fromisoformat(asof_s)
            issued_ns = int(rec["issued_at"])
            if issued_ns < 10**15 or not isinstance(item, dict):
                raise ValueError("invalid issuance")
            issued = datetime.fromtimestamp(issued_ns // 1_000_000_000, tz=KST).date()
        except (ValueError, TypeError, OverflowError):
            counts["invalid_record"] += 1
            continue
        region = str(rec["region"])
        if (item.get("region_id") != region_id or item.get("region") != region
                or item.get("asof") != asof_s):
            counts["record_mismatch"] += 1
            continue
        if item.get("assessment_status") != "ready":
            counts["held_at_issuance"] += 1
            continue
        code = kb.codes.get(region)
        if not kb.identity_verified or not code or region_id != f"kb:{code}":
            counts["identity_unverified"] += 1
            continue
        age = (issued - asof).days
        if not 0 <= age <= 8:
            counts["issuance_time_inconsistent"] += 1
            continue
        grade = str(item.get("raw_grade") or "")
        if grade not in DIRECTION:
            counts["no_directional_grade"] += 1
            continue
        counts["eligible_directional"] += 1
        target = issued + timedelta(days=HORIZON_DAYS)
        if str(code)[:5] in INCHEON_RETIRED_CODES and target >= date(2026, 7, 1):
            counts["boundary_changed"] += 1
            continue
        if kb_day < target:
            counts["pending_12_weeks"] += 1
            continue
        index = index_for(region)
        baseline = index.get(pd.Timestamp(asof))
        if not index.index.is_unique or baseline is None or pd.isna(baseline) or baseline <= 0:
            counts["missing_issue_price"] += 1
            continue
        outcome = _first_outcome(index, asof, target)
        if outcome is None:
            counts["pending_12_weeks" if kb_day < target + timedelta(days=OBSERVATION_WINDOW_DAYS)
                   else "missing_future_price"] += 1
            continue
        outcome_day, outcome_value = outcome
        pct = (outcome_value / baseline - 1) * 100
        direction = DIRECTION[grade]
        hit = pct > 0 if direction == "up" else pct < 0
        method = "/".join(str(item.get(k) or "unknown") for k in
                          ("version", "guard_version", "config_hash"))
        cohort = cohorts.setdefault(method, {"method": method, "grades": defaultdict(lambda: {
            "n": 0, "hits": 0, "paired_n": 0, "paired_hits": 0,
            "market_hits": 0, "inherited_n": 0, "regions": set(),
        })})
        row = cohort["grades"][grade]
        row["n"] += 1
        row["hits"] += int(hit)
        row["regions"].add(region_id)
        row["inherited_n"] += int(any(r.get("inherited") for r in item.get("reasons") or []
                                      if isinstance(r, dict)))
        counts["matured_scored"] += 1
        market_base = national.get(pd.Timestamp(asof))
        market_end = _first_outcome(national, asof, target)
        if (market_base is None or pd.isna(market_base) or market_base <= 0
                or market_end is None or market_end[0] != outcome_day):
            counts["market_baseline_missing"] += 1
            continue
        market_pct = (market_end[1] / market_base - 1) * 100
        row["paired_n"] += 1
        row["paired_hits"] += int(hit)
        row["market_hits"] += int(market_pct > 0 if direction == "up" else market_pct < 0)

    by_method = []
    for cohort in cohorts.values():
        grades = {}
        for grade, row in cohort["grades"].items():
            n, paired = row["n"], row["paired_n"]
            grades[grade] = {
                "evaluated": n, "distinct_regions": len(row["regions"]),
                "inherited_market_inputs": row["inherited_n"],
                "direction_match_pct": round(row["hits"] / n * 100, 1),
                "paired_market_n": paired,
                "paired_direction_match_pct": round(row["paired_hits"] / paired * 100, 1)
                                              if paired else None,
                "national_direction_match_pct": round(row["market_hits"] / paired * 100, 1)
                                                if paired else None,
                "market_difference_pp": round((row["paired_hits"] - row["market_hits"])
                                              / paired * 100, 1) if paired else None,
            }
        by_method.append({"method": cohort["method"], "grades": grades})
    by_method.sort(key=lambda row: row["method"])
    return {
        "protocol": "first-issued-region-week-12w-v1",
        "kb_asof": kb_day.isoformat(),
        "kb_identity_verified": bool(kb.identity_verified),
        "counts": dict(counts), "by_method": by_method,
        "caveats": [
            "최초 발행된 지역·관측주만 세며 같은 주 후속 수정본은 별도로 센다.",
            "발행 시각부터 12주 후 첫 KB 관측(6일 이내)을 현재 제공 중인 시계열로 비교한다. 과거 가격 원본의 수정 여부는 검증하지 못한다.",
            "시장 기준선은 같은 관측주와 평가 기간의 KB 전국 매매가격 방향이며, 차이는 인과적 기여나 미래 수익 확률이 아니다.",
            "지역·주가 겹치고 광역 수급 지표가 상속되므로 평가 건수는 독립 표본 수가 아니다. 계산 방법별 결과를 합치지 않는다.",
        ],
    }


def audit_database(kb: KBWeekly, path: Path) -> dict:
    """Read a production DB without creating or mutating any file or row."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT region_id,region,asof,issued_at,data FROM signal_assessments "
            "ORDER BY issued_at ASC,id ASC")
        return audit_records(kb, rows)
