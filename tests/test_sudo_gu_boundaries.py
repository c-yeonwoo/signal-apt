"""2026 수도권 시군구 경계 번들의 변환·식별 검증."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from realty_signal import api

_SPEC = importlib.util.spec_from_file_location("update_sudo_gu", Path(__file__).resolve().parents[1] / "scripts/update_sudo_gu.py")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
transform = _MODULE.transform


def _source_from_bundle():
    bundle = json.loads((api.WEB_DIR / "sudo_gu.geojson").read_text(encoding="utf-8"))
    source = {"type": "FeatureCollection", "features": []}
    for feature in bundle["features"]:
        props = feature["properties"]
        source["features"].append({"type": "Feature",
                                   "properties": {"sido": props["sido"], "sgg": props["code"],
                                                  "sggnm": props["name"]},
                                   "geometry": feature["geometry"]})
    return bundle, source


def test_boundary_bundle_is_reproducible_and_current():
    bundle, source = _source_from_bundle()
    assert transform(source) == bundle
    assert len(bundle["features"]) == 83
    assert bundle["metadata"]["asof"] == "2026-07-01"
    assert api._sigungu_identity_at(*api._bundled_centroids()["성남시 분당구"]) == (
        "성남시 분당구", "경기", "41135")


def test_boundary_generator_normalizes_city_ward_display_name():
    _, source = _source_from_bundle()
    row = next(f for f in source["features"] if f["properties"]["sgg"] == "41135")
    row["properties"]["sggnm"] = "성남시분당구"
    transformed = transform(source)
    actual = next(f for f in transformed["features"] if f["properties"]["code"] == "41135")
    assert actual["properties"]["name"] == "성남시 분당구"


def test_boundary_generator_rejects_duplicate_or_obsolete_incheon_code():
    _, source = _source_from_bundle()
    duplicate = copy.deepcopy(source)
    duplicate["features"].append(copy.deepcopy(duplicate["features"][0]))
    with pytest.raises(ValueError, match="invalid_feature"):
        transform(duplicate)

    obsolete = copy.deepcopy(source)
    row = next(f for f in obsolete["features"] if f["properties"]["sggnm"] == "제물포구")
    row["properties"].update({"sgg": "28110", "sggnm": "중구"})
    with pytest.raises(ValueError, match="missing_2026_incheon_districts"):
        transform(obsolete)
