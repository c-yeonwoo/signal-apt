from fastapi import HTTPException
import pytest

from realty_signal.routes import reports_v2
from realty_signal.services import discovery_v2 as discovery


def _row(key, *, price=None, area=None, stale=False, region="노원구", signal="NEUTRAL"):
    return {"key": "일반매물:" + key, "유형": "일반매물", "단지명": key,
            "지역": region, "총액": price, "ref": {"전용면적": area},
            "시그널": signal, "source": "hanbang", "fetched_at": 1790000000,
            "stale": stale}


def test_price_area_and_unknown_are_separate_tiers():
    rows = [_row("good", price=55000, area=70, signal="SELL_RISK"),
            _row("unknown", price=None, area=70, signal="STRONG_BUY"),
            _row("small", price=40000, area=50)]
    result = discovery.discover(rows, {"max_price_manwon": 60000, "min_area_m2": 60,
                                       "include_exceeded": True})
    assert [x["listing"]["name"] for x in result["groups"]["matched"]] == ["good"]
    assert [x["listing"]["name"] for x in result["groups"]["verify"]] == ["unknown"]
    assert [x["listing"]["name"] for x in result["groups"]["exceeded"]] == ["small"]
    assert result["groups"]["exceeded"][0]["tradeoff"] == "전용면적이 원하는 최소 면적보다 작습니다."
    assert result["groups"]["matched"][0]["signal_context"] == "SELL_RISK"


def test_min_rooms_is_a_verified_hard_condition_and_missing_source_stays_unknown():
    enough = _row("enough", price=55_000, area=70)
    too_few = _row("too-few", price=45_000, area=70)
    missing = _row("missing", price=50_000, area=70)
    enough["ref"]["방수"] = 3
    too_few["ref"]["방수"] = 2
    out = discovery.discover([enough, too_few, missing],
                             {"min_rooms": 3, "include_exceeded": True})
    assert [x["listing"]["name"] for x in out["groups"]["matched"]] == ["enough"]
    assert [x["listing"]["name"] for x in out["groups"]["verify"]] == ["missing"]
    assert [x["listing"]["name"] for x in out["groups"]["exceeded"]] == ["too-few"]
    assert out["groups"]["verify"][0]["verify_next"] == "매물의 방 개수를 확인하세요."
    assert out["groups"]["exceeded"][0]["tradeoff"] == "방 개수가 원하는 최소보다 적습니다."
    assert out["single_condition_relaxations"] == {"min_rooms": 1}
    for value in (0, 16, True, 2.5, "3"):
        with pytest.raises(ValueError, match="invalid_condition_value"):
            discovery.validate({"min_rooms": value})


def test_stale_price_never_passes_and_invalid_input_is_rejected():
    out = discovery.discover([_row("stale", price=45000, area=70, stale=True)],
                            {"max_price_manwon": 50000})
    assert not out["groups"]["matched"]
    assert out["groups"]["verify"][0]["constraints"][0]["status"] == "unknown"
    for spec in ({"max_price_manwon": -1}, {"limit": 51}, {"invented": 1}):
        try:
            discovery.validate(spec)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid conditions were accepted")


def test_stale_quote_above_budget_is_explicitly_unverified_and_ranked_last():
    stale = _row("old-12eok", price=120_000, stale=True, region="노원구")
    current = _row("unknown-current-price", price=None, region="강남구")
    result = discovery.discover([stale, current], {
        "max_price_manwon": 60_000, "prefer_region": "노원구", "limit": 1,
    })
    assert result["counts"]["verify"] == 2
    assert result["counts"]["exceeded"] == 0  # Old price cannot prove today's excess.
    assert result["groups"]["verify"][0]["listing"]["name"] == "unknown-current-price"
    next_page = discovery.discover([current, stale], {
        "max_price_manwon": 60_000, "prefer_region": "노원구", "limit": 1,
        "cursor": result["next_cursor"],
    })
    old = next_page["groups"]["verify"][0]
    assert old["listing"]["name"] == "old-12eok"
    assert old["constraints"][0]["status"] == "unknown"
    assert "지난 수집 호가는 설정한 상한보다 높았습니다" in old["recommendation_reason"]
    assert "현재 가격은 확인되지" in old["recommendation_reason"]
    assert "최신 호가" in old["verify_next"]
    stale["ref"]["전용면적"] = 40
    exceeded = discovery.discover([stale], {
        "max_price_manwon": 60_000, "min_area_m2": 60, "include_exceeded": True,
    })["groups"]["exceeded"][0]
    assert exceeded["tradeoff"] == "전용면적이 원하는 최소 면적보다 작습니다."
    stale["source_conflict"] = True
    conflict = discovery.discover([stale], {"max_price_manwon": 60_000})["groups"]["verify"][0]
    assert "원천 ID" in conflict["recommendation_reason"]
    assert "원천에서 단지" in conflict["verify_next"]


def test_conflicting_source_quote_is_never_a_verified_budget_match_or_top_verify():
    unknown = _row("unknown", price=None, region="강남구")
    conflict_within = _row("conflict-within", price=50_000)
    conflict_above = _row("conflict-above", price=120_000)
    for row in (conflict_within, conflict_above):
        row["source_conflict"] = True
    out = discovery.discover([conflict_above, conflict_within, unknown], {
        "max_price_manwon": 60_000, "prefer_region": "노원구",
    })
    assert out["counts"] == {"matched": 0, "verify": 3, "exceeded": 0, "explore": 0}
    assert [item["listing"]["name"] for item in out["groups"]["verify"]] == [
        "unknown", "conflict-within", "conflict-above",
    ]
    for item in out["groups"]["verify"][1:]:
        assert item["constraints"][0]["status"] == "unknown"
        assert "원천 ID" in item["recommendation_reason"]
        assert "실제 호가" in item["verify_next"]


def test_discovery_route_explains_invalid_input_without_exposing_error_codes(monkeypatch):
    monkeypatch.setattr(reports_v2, "_require_feature", lambda _name: None)
    with pytest.raises(HTTPException) as error:
        reports_v2.discovery(None, {"max_price_manwon": -1})
    assert error.value.status_code == 422
    assert error.value.detail == "호가·면적·방 개수·통근시간 등 숫자 조건을 다시 확인해 주세요."

    def unexpected(_spec):
        raise ValueError("private/source/path")

    monkeypatch.setattr(discovery, "validate", unexpected)
    with pytest.raises(HTTPException) as error:
        reports_v2.discovery(None, {})
    assert error.value.detail == "입력값을 확인하고 다시 시도해 주세요."


def test_preferred_region_ranks_first_without_excluding_other_regions():
    rows = [_row("other", price=40000, area=70, region="강남구"),
            _row("wanted", price=50000, area=70, region="노원구")]
    result = discovery.discover(rows, {"max_price_manwon": 60000,
                                       "prefer_region": "노원구"})
    matched = result["groups"]["matched"]
    assert [item["listing"]["name"] for item in matched] == ["wanted", "other"]
    assert matched[0]["preference"]["score"] == 100
    assert matched[1]["preference"]["score"] == 0
    assert result["counts"]["matched"] == 2


def test_region_code_separates_same_named_districts_and_unknown_identity():
    rows = [_row("seoul", price=50_000, region="중구"),
            _row("incheon", price=50_000, region="중구"),
            _row("no-code", price=50_000, region="중구")]
    rows[0]["지역코드"] = "11140"
    rows[1]["지역코드"] = "28110"
    result = discovery.discover(rows, {"region_code": "11140", "include_exceeded": True})
    assert [x["listing"]["name"] for x in result["groups"]["matched"]] == ["seoul"]
    assert [x["listing"]["name"] for x in result["groups"]["verify"]] == ["no-code"]
    assert [x["listing"]["name"] for x in result["groups"]["exceeded"]] == ["incheon"]
    assert result["groups"]["verify"][0]["verify_next"] == "매물의 시군구 코드를 확인하세요."
    preferred = discovery.discover(rows, {"prefer_region_code": "11140"})["groups"]["explore"]
    assert [x["preference"]["score"] for x in preferred] == [100, 0, 0]
    assert [x["preference"]["coverage"] for x in preferred] == [100, 100, 0]
    for code in ("11", "kb:11140", "1114x", 11140):
        with pytest.raises(ValueError, match="invalid_region_code"):
            discovery.validate({"region_code": code})


def test_required_region_does_not_mix_obviously_other_uncoded_districts_into_verify():
    same = _row("same-no-code", price=50_000, region="중구")
    other = _row("other-no-code", price=50_000, region="성동구")
    wrong_province = _row("wrong-province", price=50_000, region="중구")
    wrong_province["시도"] = "인천"
    coded = _row("coded", price=50_000, region="표기차이")
    coded["지역코드"] = "11140"
    result = discovery.discover([same, other, wrong_province, coded],
                                {"region_code": "11140", "include_exceeded": True},
                                region_hint={"name": "중구", "sido": "서울"})
    assert [x["listing"]["name"] for x in result["groups"]["matched"]] == ["coded"]
    assert [x["listing"]["name"] for x in result["groups"]["verify"]] == ["same-no-code"]
    assert {x["listing"]["name"] for x in result["groups"]["exceeded"]} == {
        "other-no-code", "wrong-province"}


def test_one_complex_cannot_fill_initial_diversified_candidates():
    rows = [_row("same-1", price=40000), _row("same-2", price=41000),
            _row("same-3", price=42000), _row("other", price=45000)]
    for row in rows[:3]:
        row["단지명"] = "같은 단지"
    result = discovery.discover(rows, {"max_price_manwon": 60000, "limit": 3})
    assert sorted(item["listing"]["name"] for item in result["groups"]["matched"]) == [
        "other", "같은 단지", "같은 단지"]


def test_preferences_keep_unknown_in_denominator_and_do_not_exclude():
    rows = [_row("a", price=45_000, area=None, region="노원구"),
            _row("b", price=None, area=90, region="강남구"),
            _row("c", price=55_000, area=60, region="노원구")]
    out = discovery.discover(rows, {"prefer_region": "노원구",
                                    "prefer_max_price_manwon": 50_000,
                                    "prefer_min_area_m2": 84})
    assert out["counts"]["explore"] == 3
    by_name = {x["listing"]["name"]: x["preference"] for x in out["groups"]["explore"]}
    assert (by_name["a"]["score"], by_name["a"]["coverage"]) == (67, 67)
    assert (by_name["b"]["score"], by_name["b"]["coverage"]) == (33, 67)
    assert (by_name["c"]["score"], by_name["c"]["coverage"]) == (33, 100)
    assert [x["listing"]["name"] for x in out["groups"]["explore"]] == ["a", "c", "b"]


def test_explicit_priority_changes_order_but_never_counts_unknown_as_matched():
    rows = [_row("region", price=55_000, area=None, region="노원구"),
            _row("price", price=45_000, area=None, region="강남구")]
    spec = {"prefer_region": "노원구", "prefer_max_price_manwon": 50_000,
            "prefer_min_area_m2": 84}
    by_region = discovery.discover(rows, {**spec, "priority": "region"})["groups"]["explore"]
    by_price = discovery.discover(rows, {**spec, "priority": "price"})["groups"]["explore"]
    assert [x["listing"]["name"] for x in by_region] == ["region", "price"]
    assert [x["listing"]["name"] for x in by_price] == ["price", "region"]
    assert by_price[0]["preference"]["score"] == 50
    assert by_price[0]["preference"]["coverage"] == 75
    assert by_price[0]["preference"]["details"][2]["status"] == "unknown"
    assert by_price[0]["preference"]["priority"] == "price"


def test_priority_without_corresponding_preference_has_no_hidden_bonus():
    rows = [_row("first", price=40_000), _row("second", price=45_000)]
    base = discovery.discover(rows, {"prefer_max_price_manwon": 50_000})
    unused = discovery.discover(rows, {"prefer_max_price_manwon": 50_000, "priority": "area"})
    assert [x["listing"]["name"] for x in base["groups"]["explore"]] == [
        x["listing"]["name"] for x in unused["groups"]["explore"]]
    assert unused["groups"]["explore"][0]["preference"]["priority"] == "balanced"
    for value in ("unknown", None, [], {}):
        with pytest.raises(ValueError, match="invalid_priority"):
            discovery.validate({"priority": value})


def test_exceeded_candidates_are_hidden_by_default_and_explicitly_labeled():
    rows = [_row("expensive", price=70_000, area=50)]
    spec = {"max_price_manwon": 60_000, "min_area_m2": 60}
    hidden = discovery.discover(rows, spec)
    assert hidden["counts"]["exceeded"] == 1
    assert hidden["groups"]["exceeded"] == []
    shown = discovery.discover(rows, {**spec, "include_exceeded": True})
    candidate = shown["groups"]["exceeded"][0]
    assert candidate["eligibility"] == "exceeded"
    assert candidate["tradeoff"] == ("호가가 설정한 상한보다 높습니다. "
                                    "전용면적이 원하는 최소 면적보다 작습니다.")
    assert not shown["groups"]["matched"]


def test_single_condition_relaxation_counts_only_verified_fresh_candidates():
    rows = [_row("price-only", price=70_000, area=70),
            _row("area-only", price=50_000, area=50),
            _row("both", price=70_000, area=50),
            _row("unknown-area", price=70_000, area=None),
            _row("stale-price", price=70_000, area=70, stale=True)]
    result = discovery.discover(rows, {"max_price_manwon": 60_000, "min_area_m2": 60})
    assert result["single_condition_relaxations"] == {"max_price_manwon": 1, "min_area_m2": 1}
    assert result["groups"]["exceeded"] == []


def test_unrequested_low_price_is_not_a_ranking_bonus():
    rows = [_row("z-cheap", price=10_000), _row("a-costly", price=50_000)]
    out = discovery.discover(rows, {"max_price_manwon": 60_000})
    assert [x["listing"]["name"] for x in out["groups"]["matched"]] == [
        "a-costly", "z-cheap"]
    rows[0]["fetched_at"] += 86400
    fresh = discovery.discover(rows, {"max_price_manwon": 60_000})
    assert fresh["groups"]["matched"][0]["listing"]["name"] == "z-cheap"


def test_broad_discovery_diversifies_region_without_dropping_overflow():
    rows = [_row(f"a-{i}", price=40_000, region="노원구") for i in range(5)]
    rows.append(_row("b-0", price=40_000, region="강남구"))
    first = discovery.discover(rows, {"max_price_manwon": 50_000, "limit": 5})
    names = [x["listing"]["name"] for x in first["groups"]["matched"]]
    assert "b-0" in names and "a-4" not in names
    second = discovery.discover(rows, {"max_price_manwon": 50_000, "limit": 5,
                                       "cursor": first["next_cursor"]})
    assert [x["listing"]["name"] for x in second["groups"]["matched"]] == ["a-4"]


def test_pages_are_stable_without_duplicates_and_reject_changed_snapshot():
    rows = [_row(f"listing-{i:02d}", price=40000 + i, area=70) for i in range(13)]
    spec = {"max_price_manwon": 60000, "limit": 5}
    first = discovery.discover(rows, spec)
    second = discovery.discover(list(reversed(rows)), {**spec, "cursor": first["next_cursor"]})
    third = discovery.discover(rows, {**spec, "cursor": second["next_cursor"]})
    keys = [item["listing"]["key"] for page in (first, second, third)
            for item in page["groups"]["matched"]]
    assert len(keys) == 13 == len(set(keys))
    assert [first["page"], second["page"], third["page"]] == [1, 2, 3]
    assert third["next_cursor"] is None
    changed = [dict(row) for row in rows]
    changed[0]["총액"] = 39000
    with pytest.raises(ValueError, match="stale_cursor"):
        discovery.discover(changed, {**spec, "cursor": first["next_cursor"]})


def test_cursor_cannot_change_conditions_or_seek_outside_results():
    rows = [_row(str(i), price=40000 + i) for i in range(5)]
    spec = {"max_price_manwon": 60000, "limit": 2}
    cursor = discovery.discover(rows, spec)["next_cursor"]
    for query in ({**spec, "min_area_m2": 60, "cursor": cursor},
                  {**spec, "priority": "price", "cursor": cursor},
                  {**spec, "cursor": "not-base64!"},
                  {**spec, "cursor": cursor + "x"}):
        with pytest.raises(ValueError, match="invalid_cursor"):
            discovery.discover(rows, query)
