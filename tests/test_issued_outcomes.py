"""Actually issued grades must be audited separately from hindsight reconstruction."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
from typer.testing import CliRunner

from realty_signal import cli, db, store
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.services import issued_outcomes
from realty_signal.time_kst import KST


def _kb(*, verified: bool = True) -> KBWeekly:
    weeks = pd.date_range("2026-01-05", periods=22, freq="W-MON")
    rows = [(day, region, "sale_change", value)
            for day in weeks
            for region, value in (("테스트구", 0.2), ("전국", -0.1))]
    return KBWeekly(pd.DataFrame(rows, columns=["date", "region", "metric", "value"]),
                    codes={"테스트구": "11110"}, identity_verified=verified)


def _record(asof: str = "2026-01-05", *, grade: str = "BUY", status: str = "ready",
            issue_weeks: int = 0, region_id: str = "kb:11110", method: str = "cfg-1") -> dict:
    asof_date = datetime.fromisoformat(asof).replace(tzinfo=KST)
    issued = asof_date + timedelta(days=1, weeks=issue_weeks)
    data = {"region_id": region_id, "region": "테스트구", "asof": asof,
            "assessment_status": status, "raw_grade": grade,
            "version": "v1", "guard_version": "g1", "config_hash": method,
            "reasons": [{"inherited": True}]}
    return {"region_id": region_id, "region": "테스트구", "asof": asof,
            "issued_at": int(issued.timestamp() * 1_000_000_000),
            "data": json.dumps(data, ensure_ascii=False)}


def test_first_issued_week_is_scored_against_same_period_national_direction():
    first = _record()
    correction = {**first, "issued_at": first["issued_at"] + 1,
                  "data": _record(grade="SELL_RISK")["data"]}
    result = issued_outcomes.audit_records(_kb(), [first, correction])
    counts = result["counts"]
    assert counts["issued_rows"] == 2
    assert counts["later_same_week_revisions"] == 1
    assert counts["matured_scored"] == 1
    grade = result["by_method"][0]["grades"]["BUY"]
    assert grade == {"evaluated": 1, "distinct_regions": 1, "inherited_market_inputs": 1,
                     "direction_match_pct": 100.0, "paired_market_n": 1,
                     "paired_direction_match_pct": 100.0,
                     "national_direction_match_pct": 0.0, "market_difference_pp": 100.0}
    assert "테스트구" not in json.dumps(result, ensure_ascii=False)


def test_pending_held_unverified_and_method_cohorts_are_separate():
    records = [_record(status="held"), _record("2026-02-02", method="cfg-2"),
               _record("2026-03-02", method="cfg-4"),
               _record("2026-05-18", method="cfg-3"),
               _record("2026-03-02", region_id="unverified:테스트구")]
    result = issued_outcomes.audit_records(_kb(), records)
    assert result["counts"]["held_at_issuance"] == 1
    assert result["counts"]["pending_12_weeks"] == 1
    assert result["counts"]["identity_unverified"] == 1
    assert {row["method"].split("/")[-1] for row in result["by_method"]} == {"cfg-2", "cfg-4"}
    unverified = issued_outcomes.audit_records(_kb(verified=False), [_record()])
    assert unverified["counts"]["identity_unverified"] == 1
    assert unverified["by_method"] == []


def test_missing_week_cannot_be_silently_compounded_into_12_week_outcome():
    kb = _kb()
    kb.long = kb.long[~((kb.long["region"] == "테스트구")
                        & (kb.long["date"] == pd.Timestamp("2026-02-16")))]
    result = issued_outcomes.audit_records(kb, [_record()])
    assert result["counts"]["missing_future_price"] == 1
    assert result["by_method"] == []


def test_retired_boundary_crossing_is_not_treated_as_same_region_outcome():
    kb = _kb()
    kb.codes["테스트구"] = "28110"
    result = issued_outcomes.audit_records(
        kb, [_record("2026-04-13", region_id="kb:28110")])
    assert result["counts"]["boundary_changed"] == 1
    assert "pending_12_weeks" not in result["counts"]


def test_read_only_database_and_cli_emit_aggregate_without_mutation(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setattr(db, "DB", db_path)
    db._migrated[0] = False
    first = _record()
    with db.conn() as connection:
        connection.execute("INSERT INTO signal_assessments VALUES(?,?,?,?,?,?)",
                           ("id-1", first["region_id"], first["region"], first["asof"],
                            first["issued_at"], first["data"]))
    before = db_path.read_bytes()
    result = issued_outcomes.audit_database(_kb(), db_path)
    assert result["counts"]["matured_scored"] == 1
    assert db_path.read_bytes() == before
    monkeypatch.setattr(store, "load", lambda: _kb())
    cli_result = CliRunner().invoke(cli.app, ["issued-outcomes"])
    assert cli_result.exit_code == 0
    assert json.loads(cli_result.output)["counts"]["matured_scored"] == 1
    assert db_path.read_bytes() == before
