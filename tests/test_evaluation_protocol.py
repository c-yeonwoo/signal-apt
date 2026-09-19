from datetime import date, timedelta

import pandas as pd
import pytest

from realty_signal.brain import config_store, evaluation, outcomes
from realty_signal.signals import engine
from realty_signal.ingest.kb_weekly import KBWeekly


def test_immature_and_missing_calendar_endpoint_are_not_12_week_returns():
    s = pd.Series([100, 110], index=pd.date_range("2026-01-05", periods=2, freq="W-MON"))
    assert engine._forward_pct(s, s.index[0]) is None
    mature = pd.Series(range(100, 114), index=pd.date_range("2026-01-05", periods=14, freq="W-MON"))
    assert engine._forward_pct(mature, mature.index[0]) == 12
    assert engine._forward_pct(mature.drop(mature.index[12]), mature.index[0]) is None


def test_stronger_later_signal_does_not_relabel_start(monkeypatch):
    dates = pd.date_range("2020-01-06", periods=20, freq="W-MON")
    kb = KBWeekly(pd.DataFrame([("A", d, "sale_change", .1) for d in dates],
                              columns=["region", "date", "metric", "value"]), {"A": "11110"})
    states = iter(["BUY"]*5 + ["STRONG_BUY"]*15)
    monkeypatch.setattr(engine, "_classify", lambda *_: (next(states), []))
    intervals = engine.signal_history(kb, "A")
    assert intervals[0]["signal"] == "BUY" and intervals[1]["signal"] == "STRONG_BUY"
    assert intervals[1]["start"] == str(dates[5].date())
    assert intervals[1]["closed"] is False
    assert intervals[1]["after12w_pct"] is None


def test_same_date_data_revision_has_different_cache_key():
    dates = pd.date_range("2020-01-06", periods=15, freq="W-MON")
    def kb(v):
        return KBWeekly(pd.DataFrame([("A", d, "sale_change", v) for d in dates],
                                    columns=["region", "date", "metric", "value"]), {"A": "11110"})
    assert engine._kb_key(kb(.1)) != engine._kb_key(kb(-.1))


def test_outcome_revisions_are_append_only_and_idempotent():
    for sig in ("BUY", "STRONG_BUY", "BUY"):
        outcomes.append_region_snapshot("2026-09-07", [{"region": "A", "signal": sig}])
    revisions = outcomes.list_revisions()
    assert len(revisions) == 2
    assert {r["regions"]["A"]["signal"] for r in revisions} == {"BUY", "STRONG_BUY"}
    assert all(r["published_at"] is None and not r["vintage_verified"] for r in revisions)


def _records():
    out = []
    start = date(2018, 1, 1)
    for week in range(420):
        at = start+timedelta(weeks=week)
        for cluster in ("A", "B", "C"):
            out.append({"decision_at": at.isoformat(), "available_at": at.isoformat(),
                        "outcome_at": (at+timedelta(weeks=12)).isoformat(),
                        "return": 2, "baseline_return": 1, "region_cluster": cluster,
                        "config_hash": evaluation.params_hash({}), "vintage_verified": True,
                        "evidence_id": f"{at}:{cluster}"})
    return out


def test_purged_holdout_and_cluster_bootstrap_are_reproducible():
    protocol = evaluation.Protocol("2020-12-31", "2022-12-31", "2025-12-31")
    a = evaluation.evaluate(_records(), protocol, params={})
    assert a == evaluation.evaluate(_records(), protocol, params={})
    assert a["sufficient"] and a["excess_ci95"] == [1, 1]
    assert a["excluded"]["purged_boundary"] > 0
    # The final immature cohort is explicitly excluded and cannot be promoted.
    assert a["excluded"]["immature_or_wrong_horizon"] > 0
    assert not a["promotable"]


def test_unknown_vintage_and_future_information_cannot_pass():
    protocol = evaluation.Protocol("2020-12-31", "2022-12-31", "2025-12-31")
    records = _records()
    records[0]["available_at"] = "2030-01-01"
    records[1]["vintage_verified"] = False
    out = evaluation.evaluate(records, protocol, params={})
    assert out["excluded"]["future_information"] == 1
    assert out["excluded"]["unverified_vintage_or_config"] == 1
    assert not out["promotable"]


def test_config_promotion_requires_matching_passed_holdout():
    with pytest.raises(ValueError):
        config_store.apply_config("unvalidated", {"buyeridx_strong": 80})


def test_duplicate_decisions_cannot_inflate_sample_size():
    records = _records()
    protocol = evaluation.Protocol("2020-12-31", "2022-12-31", "2025-12-31")
    result = evaluation.evaluate(records + records[:1], protocol, params={})
    assert result["excluded"]["duplicate_decision"] == 1
    assert not result["promotable"]
