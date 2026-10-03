from datetime import date

import pandas as pd

from realty_signal import db
from realty_signal.ingest import kb_datahub
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.services import signal_assessment as sa
from realty_signal.signals.engine import SignalConfig


def _kb(changes=(0.1, 0.1, 0.1, 0.1), code="1114000000"):
    dates = pd.date_range("2026-09-07", periods=4, freq="W-MON")
    rows = [(day, "중구", "sale_change", change) for day, change in zip(dates, changes)]
    rows += [(day, "중구", "jeonse_supply", 180) for day in dates]
    rows += [(day, "중구", "buyer_superiority", 80) for day in dates]
    rows += [(day, "강북14개구", "jeonse_supply", 180) for day in dates]
    rows += [(day, "강북14개구", "buyer_superiority", 80) for day in dates]
    return KBWeekly(pd.DataFrame(rows, columns=["date", "region", "metric", "value"]),
                    {"중구": code})


def _row(raw="BUY"):
    return {"region": "중구", "signal": raw, "전세수급": 180,
            "매수우위지수": 80, "수급출처": "강북14개구"}


def test_missing_price_and_negative_price_hold_a_raw_buy():
    c = SignalConfig()
    missing = sa.build(_kb((0.1, 0.1, 0.1)), _row(), c,
                       asof=date(2026, 9, 28), today=date(2026, 9, 28))
    assert missing["raw_grade"] == "BUY"
    assert missing["display_grade"] == "판단 보류"
    assert "sale_weeks_incomplete" in missing["risk_flags"]
    falling = sa.build(_kb((-0.15,) * 4), _row(), c,
                       asof=date(2026, 9, 28), today=date(2026, 9, 28))
    assert falling["display_grade"] == "판단 보류"
    assert "price_direction_conflict" in falling["risk_flags"]
    assert falling["reasons"][2]["unit"] == "%/주"


def test_legacy_jung_gu_identity_is_held_and_issued_history_is_immutable():
    c = SignalConfig()
    old = sa.build(_kb(code="2811000000"), _row(), c,
                   asof=date(2026, 9, 28), today=date(2026, 9, 28))
    assert old["region_id"] == "ambiguous:중구"
    assert "region_identity_ambiguous" in old["risk_flags"]
    good = sa.build(_kb(), _row(), c, asof=date(2026, 9, 28), today=date(2026, 9, 28))
    assert good["assessment_status"] == "ready"
    assert sa.issue_many([good, good]) == 1
    cdb = db.conn()
    try:
        assert cdb.execute("SELECT count(*) FROM signal_assessments").fetchone()[0] == 1
    finally:
        cdb.close()


def test_datahub_disambiguates_incheon_jung_gu(monkeypatch):
    def source(_path, params):
        return {"날짜리스트": ["20260928"], "데이터리스트": [
            {"지역명": "중구", "지역코드": "1114000000", "dataList": [0.1]},
            {"지역명": "중구", "지역코드": "2811000000", "dataList": [-0.1]},
        ]}
    monkeypatch.setattr(kb_datahub, "_get", source)
    codes = {}
    rows = kb_datahub._change_rows("01", "sale_change", code_sink=codes)
    assert {r[1] for r in rows} == {"중구", "인천 중구"}
    assert codes == {"중구": "1114000000", "인천 중구": "2811000000"}


def test_previous_issuance_explains_same_date_revision_without_mutating_original():
    previous = sa.build(_kb(), _row("BUY"), SignalConfig(),
                        asof=date(2026, 9, 28), today=date(2026, 9, 28))
    assert sa.issue_many([previous]) == 1
    current = sa.build(_kb((0.1, 0.1, 0.1, 0.2)), _row("STRONG_BUY"), SignalConfig(),
                       asof=date(2026, 9, 28), today=date(2026, 9, 28))
    comparison = sa.with_previous(current)
    assert comparison["change"]["type"] == "source_revision"
    assert set(comparison["change"]["changed_reasons"]) == {"sale_momentum", "grade"}
    assert comparison["reasons"][2]["previous_value"] == 0.1
    assert "previous_value" not in current["reasons"][2]


def test_same_market_data_can_become_held_when_source_ages():
    previous = sa.build(_kb(), _row(), SignalConfig(),
                        asof=date(2026, 9, 28), today=date(2026, 9, 28))
    sa.issue_many([previous])
    current = sa.build(_kb(), _row(), SignalConfig(),
                       asof=date(2026, 9, 28), today=date(2026, 10, 20))
    assert current["assessment_status"] == "held"
    comparison = sa.with_previous(current)
    assert comparison["change"]["type"] == "freshness_change"
    assert comparison["change"]["changed_reasons"] == ["safety_status"]
