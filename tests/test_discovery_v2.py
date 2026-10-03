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
    assert [item["listing"]["name"] for item in result["groups"]["matched"]] == [
        "같은 단지", "같은 단지", "other"]


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
                  {**spec, "cursor": "not-base64!"},
                  {**spec, "cursor": cursor + "x"}):
        with pytest.raises(ValueError, match="invalid_cursor"):
            discovery.discover(rows, query)
