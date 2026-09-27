"""경로 분석/저장 허가 없을 때 제3자 응답을 수집·재사용하지 않는다."""

import pandas as pd

from realty_signal import api, config, store
from realty_signal.ingest import locality
from realty_signal.services import shortlist


def test_analysis_routes_are_disabled_without_approval(monkeypatch):
    monkeypatch.delenv("ODSAY_ANALYSIS_APPROVED", raising=False)
    monkeypatch.setenv("ODSAY_KEY", "test-key")
    monkeypatch.setattr(locality, "_fetch", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("called")))
    assert locality.transit_min(37.5, 127.0) == (None, None)
    assert locality.transit_between(127.0, 37.5, 127.1, 37.6, purpose="analysis") is None
    assert shortlist._region_commute("서울", (37.5, 127.0)) is None


def test_direct_route_does_not_persist_without_cache_approval(monkeypatch):
    monkeypatch.delenv("ODSAY_CACHE_APPROVED", raising=False)
    monkeypatch.setattr(locality, "transit_between", lambda *_a, **_k: {"min": 23})
    monkeypatch.setattr(api.db, "kv_get", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache read")))
    monkeypatch.setattr(api.db, "kv_set", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache write")))
    assert api.transit_ep(127.0, 37.5, 127.1, 37.6) == {"available": True, "route": {"min": 23}}


def test_legacy_or_unlicensed_locality_cache_is_not_exposed(tmp_path, monkeypatch):
    path = tmp_path / "locality.parquet"
    pd.DataFrame([{"region": "서울", "accessibility": -999, "저평가도": 80}]).to_parquet(path)
    monkeypatch.delenv("ODSAY_ANALYSIS_APPROVED", raising=False)
    monkeypatch.delenv("ODSAY_CACHE_APPROVED", raising=False)
    assert store.load_localities(path).empty
    monkeypatch.setenv("ODSAY_ANALYSIS_APPROVED", "1")
    monkeypatch.setenv("ODSAY_CACHE_APPROVED", "1")
    assert store.load_localities(path).empty  # 구 버전은 999분 대체값을 사용했음
    pd.DataFrame([{"region": "서울", "accessibility": -30,
                   "자료검증버전": store.LOCALITY_MODEL_VERSION, "저평가도": 5}]).to_parquet(path)
    assert store.load_localities(path)["region"].tolist() == ["서울"]


def test_no_permission_is_visible_to_client(monkeypatch):
    monkeypatch.delenv("ODSAY_ANALYSIS_APPROVED", raising=False)
    monkeypatch.delenv("ODSAY_CACHE_APPROVED", raising=False)
    monkeypatch.setattr(store, "load_localities", lambda: pd.DataFrame())
    assert api.undervalued()["reason"] == "source_permission_required"


def test_config_approval_is_explicit(monkeypatch):
    monkeypatch.delenv("ODSAY_ANALYSIS_APPROVED", raising=False)
    monkeypatch.delenv("ODSAY_CACHE_APPROVED", raising=False)
    assert not config.odsay_analysis_approved() and not config.odsay_cache_approved()
    monkeypatch.setenv("ODSAY_ANALYSIS_APPROVED", "true")
    assert config.odsay_analysis_approved() and not config.odsay_cache_approved()
