from realty_signal.services import discovery_v2 as discovery
import pytest


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
    assert result["groups"]["matched"][0]["signal_context"] == "SELL_RISK"


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
