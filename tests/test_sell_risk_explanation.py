"""A displayed SELL_RISK overlay must show the factors that changed its grade."""

import json
from datetime import date

import pandas as pd

from realty_signal.services import signal_assessment
from realty_signal.services import market_data as md
from realty_signal.routes import reports_v2
from realty_signal.signals.engine import SignalConfig, evaluate
from test_signal_assessment_v2 import _kb


def _market(changes=(-0.3,) * 4):
    kb = _kb(changes)
    kb.long.loc[kb.long["metric"] == "jeonse_supply", "value"] = 120
    kb.long.loc[kb.long["metric"] == "buyer_superiority", "value"] = 55
    return kb


def _assessment(kb, **kwargs):
    row = evaluate(kb, SignalConfig(), **kwargs)
    source = json.loads(row[row["region"] == "중구"].iloc[0].to_json(force_ascii=False))
    return source, signal_assessment.build(kb, source, SignalConfig(),
                                           asof=date(2026, 9, 28), today=date(2026, 9, 28))


def test_volume_and_price_overlay_has_the_same_reasons_as_the_grade():
    row, assessed = _assessment(_market(), volumes={"중구": {"거래량비": 0.7}})
    assert row["signal"] == "SELL_RISK"
    assert row["매도보정"]["applied"] is True
    assert row["매도보정"]["score"] == 2
    assert assessed["assessment_status"] == "ready"
    assert assessed["sell_overlay"]["factor_ids"] == ["sale_decline", "transaction_volume_low"]
    reasons = {reason["reason_id"]: reason for reason in assessed["reasons"]}
    assert reasons["transaction_volume_low"]["value"] == 0.7
    assert reasons["transaction_volume_low"]["role"] == "counterevidence"
    assert "거래량 위축" in assessed["summary"]


def test_non_price_endgame_glut_overlay_explains_supply_cycle_and_rate():
    kb = _market(changes=(0.0,) * 4)
    row, assessed = _assessment(
        kb, supply=pd.DataFrame([
            {"region": "중구", "supply_pressure": 1.5}]),
        macro={"대출금리": [3.0] * 6 + [3.2]},
        regime={"endgame": True, "regions": {}})
    assert row["signal"] == "SELL_RISK"
    assert row["매도보정"]["applied"] is True
    assert row["매도보정"]["score"] == 2.0
    assert assessed["assessment_status"] == "ready"
    reasons = {reason["reason_id"]: reason for reason in assessed["reasons"]}
    assert {"supply_pressure", "regional_cycle_late", "national_rate_rising"} <= set(reasons)
    assert reasons["national_rate_rising"]["source_region"] == "전국"
    assert "입주물량 부담" in assessed["summary"]


def test_buy_grade_is_not_rewritten_by_bear_overlay_explanation():
    kb = _market(changes=(0.3,) * 4)
    kb.long.loc[kb.long["metric"] == "jeonse_supply", "value"] = 180
    row, assessed = _assessment(kb, volumes={"중구": {"거래량비": 0.7}})
    assert row["signal"] == "BUY"
    assert row["매도보정"] is None
    assert "sell_overlay" not in assessed


def test_region_report_exposes_overlay_factors_to_the_reader(monkeypatch):
    kb = _market()
    source = evaluate(kb, SignalConfig(), volumes={"중구": {"거래량비": 0.7}})
    monkeypatch.setattr(md, "kb", lambda: kb)
    monkeypatch.setattr(md, "signals_df", lambda: source)
    monkeypatch.setattr(md, "signal_config", SignalConfig)
    monkeypatch.setattr(md, "region_for_ref", lambda ref: "중구")
    original = signal_assessment.build
    monkeypatch.setattr(signal_assessment, "build", lambda kb, row, config: original(
        kb, row, config, asof=date(2026, 9, 28), today=date(2026, 9, 28)))
    monkeypatch.setattr(signal_assessment, "with_previous", lambda assessment: assessment)
    report = json.loads(reports_v2.region_report("중구").body)
    assert report["assessment"]["display_grade"] == "매도주의"
    assert "거래량 위축" in report["assessment"]["summary"]
    assert "transaction_volume_low" in {r["reason_id"] for r in report["cautions"]}


def test_same_week_explanation_enrichment_is_not_labeled_source_revision():
    kb = _market()
    row, current = _assessment(kb, volumes={"중구": {"거래량비": 0.7}})
    previous = signal_assessment.build(
        kb, {**row, "매도보정": None}, SignalConfig(),
        asof=date(2026, 9, 28), today=date(2026, 9, 28))
    assert signal_assessment.issue_many([previous]) == 1
    compared = signal_assessment.with_previous(current)
    assert compared["change"]["type"] == "explanation_change"
    assert compared["change"]["changed_reasons"] == ["transaction_volume_low"]
