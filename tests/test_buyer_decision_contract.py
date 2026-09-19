from dataclasses import replace

from realty_signal import buying_power as bp, regulation
from realty_signal.services import buyer_decision as decision, recommend


def _row():
    return {"유형": "급매", "단지명": "A", "지역": "강남구", "가격출처": "매물가",
            "추정가": 10000, "fetched_at": "2026-09-19T00:00:00Z", "source": "synthetic",
            "자금": {"가능": True, "확인필요": False}}


def test_engine_feasibility_never_becomes_bank_approval():
    result = decision.build(_row(), bp.Params(capital=50000), uid=1)
    assert result["decision"]["feasibility"] == "conditional"
    assert result["decision"]["unknowns"]
    assert result["decision"]["evidence_ids"] == [result["evidence"][0]["id"]]


def test_modeled_price_and_missing_finance_are_unknown():
    row = _row() | {"가격출처": "지역평단추정", "자금": None}
    result = decision.build(row, bp.Params(capital=50000))
    assert result["decision"]["feasibility"] == "unknown"
    assert result["evidence"][0]["price_kind"] == "modeled"


def test_blocking_finance_cannot_be_hidden_by_good_price():
    result = decision.build(_row() | {"자금": {"가능": False}}, bp.Params(capital=50000))
    assert result["decision"]["feasibility"] == "infeasible"
    assert result["decision"]["blocking_reasons"]


def test_identity_tracks_price_profile_policy_and_owner(monkeypatch):
    p = bp.Params(capital=50000, income=8000)
    baseline = decision.build(_row(), p, uid=1)["decision"]["id"]
    assert baseline == decision.build(_row(), p, uid=1)["decision"]["id"]
    for row, params, uid in [(_row() | {"추정가": 10001}, p, 1),
                             (_row(), replace(p, reserve_cash=1), 1), (_row(), p, 2)]:
        assert baseline != decision.build(row, params, uid=uid)["decision"]["id"]
    monkeypatch.setattr(regulation, "MAX_YEARS_METRO", 29)
    assert baseline != decision.build(_row(), p, uid=1)["decision"]["id"]


def test_asof_is_not_false_policy_verification():
    manifest = regulation.policy_manifest()
    assert manifest["status"] == "unverified"
    assert all(r["verified_at"] is None and r["sources"] == [] for r in manifest["rules"])


def test_candidate_region_finance_overrides_indicative_global_budget():
    rows = recommend.rank_listings([{"유형": "급매", "총액": 40000}], budget=30000,
        pyeong=25, loc_price_of=lambda _: None,
        finance_of=lambda *_: {"가능": True, "확인필요": False})
    assert rows[0]["예산내"]


def test_unknown_candidate_before_known_infeasible_candidate():
    rows = recommend.rank_listings([{"유형": "급매", "총액": 40000}, {"유형": "청약"}],
        budget=30000, pyeong=25, loc_price_of=lambda _: None)
    assert rows[0]["예산확인필요"]


def test_generation_time_does_not_defeat_ai_cache_but_source_revision_does():
    one = {"generated_at": "now", "cached": False, "source": {"fetched_at": "2026-09-01"}}
    two = one | {"generated_at": "later", "cached": True}
    assert decision.fingerprint(decision.cache_payload(one)) == decision.fingerprint(decision.cache_payload(two))
    two["source"] = {"fetched_at": "2026-09-02"}
    assert decision.fingerprint(decision.cache_payload(one)) != decision.fingerprint(decision.cache_payload(two))
