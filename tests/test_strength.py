"""시장강도 프록시 · entity · pipeline 테스트."""

from __future__ import annotations

import json

from realty_signal.entities import Provenance, RegionEntity
from realty_signal.ingest import pipeline
from realty_signal.signals.strength import market_strength
from realty_signal.signals.timing import region_timing


def test_market_strength_hot_volume():
    r = market_strength(volume_ratio=1.4, quicksale_count=5, signal="BUY")
    assert r.score >= 70
    assert r.label in ("활발", "강세")
    assert "거래량비" in r.reasons[0] or any("거래량" in x for x in r.reasons)


def test_market_strength_weak():
    r = market_strength(volume_ratio=0.5, quicksale_count=0, signal="SELL_RISK")
    assert r.score <= 30
    assert r.label in ("위축", "침체")


def test_region_timing_uses_strength():
    a = region_timing("BUY", market_strength=80)
    b = region_timing("BUY", market_strength=20)
    assert a.score > b.score


def test_region_entity_dict():
    e = RegionEntity(
        region="강남구", signal="BUY", market_strength=70, market_strength_label="활발",
        provenance=Provenance(source="test", status="partial", confidence=0.5),
    )
    d = e.to_dict()
    assert d["region"] == "강남구"
    assert d["market_strength"] == 70
    assert d["provenance"]["status"] == "partial"


def test_build_market_strength(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.store, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pipeline.store, "VOLUME_FILE", tmp_path / "volume.json")
    monkeypatch.setattr(pipeline, "STRENGTH_FILE", tmp_path / "market_strength.json")
    monkeypatch.setattr(pipeline, "QUICKSALE_FILE", tmp_path / "quicksale.json")
    (tmp_path / "volume.json").write_text(json.dumps({
        "강남구": {"거래량비": 1.3, "dates": [], "counts": []},
        "마포구": {"거래량비": 0.7, "dates": [], "counts": []},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "quicksale.json").write_text(json.dumps({
        "listings": [{"지역": "강남구"}, {"지역": "강남구"}, {"지역": "강남구"}],
    }, ensure_ascii=False), encoding="utf-8")

    def _vols():
        return json.loads((tmp_path / "volume.json").read_text(encoding="utf-8"))

    monkeypatch.setattr(pipeline.store, "load_volumes", _vols)
    out = pipeline.build_market_strength({"강남구": "BUY", "마포구": "WATCH"})
    assert out["count"] == 2
    assert out["regions"]["강남구"]["시장강도"] > out["regions"]["마포구"]["시장강도"]
    health = pipeline.cache_health()
    assert "sources" in health
