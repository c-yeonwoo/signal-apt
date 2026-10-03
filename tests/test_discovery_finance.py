"""Finance discovery must not turn unverified regulation into buyer eligibility."""

from realty_signal import buying_power, regulation
from realty_signal.services import discovery_v2, discovery_finance
from realty_signal.services.buyer_decision import finance_fingerprint

from test_discovery_v2 import _row


def _profile(*, capital=100_000, income=10_000):
    profile = {"가용자본": capital, "연소득": income, "매수지역": "노원구"}
    profile["매수력"] = {"가정버전": finance_fingerprint(
        buying_power.params_from_profile(profile, sido="서울"))}
    return profile


def test_unverified_policy_monthly_estimate_stays_in_verify():
    scenario = discovery_finance.FinanceScenario(_profile(), sido_of=lambda _region: "서울")
    assert scenario.policy["status"] == "unverified"
    out = discovery_v2.discover([_row("one", price=50_000, area=70)],
                                {"max_monthly_manwon": 200}, finance_of=scenario.for_row)
    assert not out["groups"]["matched"]
    item = out["groups"]["verify"][0]
    assert item["finance"]["monthly_manwon"] == 0
    assert item["finance"]["status"] == "policy_unverified"
    assert item["constraints"][0]["status"] == "unknown"


def test_missing_or_changed_profile_cannot_produce_monthly_pass():
    row = _row("one", price=50_000, area=70)
    for profile, status in (({}, "no_confirmed_profile"),
                            ({**_profile(), "가용자본": 90_000}, "stale_profile")):
        scenario = discovery_finance.FinanceScenario(profile, sido_of=lambda _region: "서울")
        out = discovery_v2.discover([row], {"max_monthly_manwon": 200},
                                    finance_of=scenario.for_row)
        assert not out["groups"]["matched"]
        assert out["groups"]["verify"][0]["finance"]["status"] == status
        assert "monthly_manwon" not in out["groups"]["verify"][0]["finance"]


def test_confirmed_cash_buyer_with_missing_income_can_see_reference_payment():
    params = buying_power.Params(capital=100_000, income=None, region="노원구", sido="서울")
    profile = {"가용자본": 100_000, "연소득": 0, "매수지역": "노원구",
               "매수력": {"가정버전": finance_fingerprint(params)}}
    scenario = discovery_finance.FinanceScenario(profile, sido_of=lambda _region: "서울")
    assert scenario.status == "ready"
    assert scenario.for_row(_row("one", price=50_000, area=70))["monthly_manwon"] == 0


def test_unverified_policy_does_not_fail_infeasible_candidate():
    scenario = discovery_finance.FinanceScenario(_profile(capital=1_000),
                                                 sido_of=lambda _region: "서울")
    out = discovery_v2.discover([_row("one", price=50_000, area=70)],
                                {"max_monthly_manwon": 200, "include_exceeded": True},
                                finance_of=scenario.for_row)
    assert not out["groups"]["exceeded"]
    assert out["groups"]["verify"][0]["finance"]["possible"] is False


def test_verified_test_policy_distinguishes_pass_and_insufficient_cash(monkeypatch):
    monkeypatch.setattr(regulation, "policy_manifest", lambda: {
        "version": "synthetic-verified", "status": "verified", "declared_asof": "test"})
    good = discovery_finance.FinanceScenario(_profile(), sido_of=lambda _region: "서울")
    bad = discovery_finance.FinanceScenario(_profile(capital=1_000), sido_of=lambda _region: "서울")
    row = _row("one", price=50_000, area=70)
    matched = discovery_v2.discover([row], {"max_monthly_manwon": 200},
                                    finance_of=good.for_row)
    assert matched["groups"]["matched"][0]["constraints"][0]["status"] == "pass"
    exceeded = discovery_v2.discover([row], {"max_monthly_manwon": 200, "include_exceeded": True},
                                     finance_of=bad.for_row)
    assert exceeded["groups"]["exceeded"][0]["constraints"][0]["status"] == "fail"


def test_rounded_payment_does_not_pass_exact_monthly_ceiling():
    estimate = {"status": "assessed", "monthly_manwon": 200,
                "monthly_exact_manwon": 200.4, "possible": True}
    result = discovery_v2.discover([_row("one", price=50_000, area=70)],
                                   {"max_monthly_manwon": 200, "include_exceeded": True},
                                   finance_of=lambda _row: estimate)
    assert result["groups"]["exceeded"][0]["constraints"][0]["status"] == "fail"


def test_ambiguous_region_never_uses_optimistic_local_policy(monkeypatch):
    monkeypatch.setattr(regulation, "policy_manifest", lambda: {
        "version": "synthetic-verified", "status": "verified", "declared_asof": "test"})
    scenario = discovery_finance.FinanceScenario(_profile(), sido_of=lambda _region: "서울")
    finance = scenario.for_row(_row("one", price=50_000, area=70, region="중구"))
    assert finance["status"] == "region_unknown"
    wrong_sido = discovery_finance.FinanceScenario(_profile(), sido_of=lambda _region: "인천")
    assert wrong_sido.for_row(_row("two", price=50_000, area=70))["status"] != "assessed"
    assert scenario.for_row(_row("three", price=50_000, area=float("nan")))["status"] == "area_unknown"
