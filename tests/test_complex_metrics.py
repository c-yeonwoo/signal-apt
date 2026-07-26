"""단지 전세가율·갭 추출 및 단지시그널 가중(전세 유무) 단위 테스트."""

from __future__ import annotations

from realty_signal.services.complex_signal import (
    complex_signal,
    main_flat_metrics,
    region_price_context,
)


def test_main_flat_metrics_picks_busiest_pyeong():
    d = {
        "평형별": [
            {"평형": 24, "매매건수": 2, "전세가율": 55, "갭": 20000},
            {"평형": 34, "매매건수": 10, "전세가율": 72, "갭": 15000, "최근매매": 80000},
        ],
        "매매추이": [{"평단가": 100}, {"평단가": 110}, {"평단가": 105}],
    }
    m = main_flat_metrics(d)
    assert m["주력평형"] == 34
    assert m["전세가율"] == 72
    assert m["갭"] == 15000
    assert m["spark"] == [100, 110, 105]


def test_main_flat_metrics_missing_jeonse():
    m = main_flat_metrics({"평형별": [{"평형": 30, "매매건수": 3}], "매매추이": []})
    assert m["전세가율"] is None and m["갭"] is None
    assert m["spark"] == []


def test_complex_signal_reduces_comp_weight_without_jeonse(monkeypatch):
    monkeypatch.setattr(
        "realty_signal.services.complex_signal.md.regime",
        lambda: {"regions": {"마포구": {}}, "endgame": False},
    )
    monkeypatch.setattr("realty_signal.services.complex_signal.uv_map", lambda: {})
    base = {
        "추세pct": 0,
        "총거래": 24,
        "매매추이": [{"평단가": 100}, {"평단가": 100}, {"평단가": 100}],
        "평형별": [{"평형": 30, "매매건수": 5, "전세가율": 75}],
    }
    with_j = complex_signal("마포구", base, "BUY", None)
    no_j = complex_signal(
        "마포구",
        {**base, "평형별": [{"평형": 30, "매매건수": 5}]},
        "BUY",
        None,
    )
    assert "근거부족" not in with_j
    assert no_j.get("근거부족") == ["전세가율"]
    assert "주의" in no_j
    assert with_j["분해"]["단지"] >= no_j["분해"]["단지"]
    assert with_j["점수"] >= no_j["점수"]


def test_region_price_context_labels(monkeypatch):
    monkeypatch.setattr(
        "realty_signal.services.complex_signal.locality_map",
        lambda: {"노원구": {"price": 3000, "저평가도": 12.5, "적정가": 3400}},
    )
    cheap = region_price_context("노원구", 2500)
    assert cheap["지역대비pct"] == -16.7
    assert cheap["지역대비라벨"] == "지역보다 저렴"
    dear = region_price_context("노원구", 3600)
    assert dear["지역대비pct"] == 20.0
    assert dear["지역대비라벨"] == "지역보다 비쌈"
    mid = region_price_context("노원구", 3050)
    assert mid["지역대비라벨"] == "지역 중위 수준"


def test_listing_pyeong_from_auction_and_quicksale():
    from realty_signal.api import _listing_pyeong

    assert _listing_pyeong("경매", {"전용면적": 84.9}, {}) == 25.7
    assert _listing_pyeong("급매", {"평형": 34}, {}) == 34.0
    assert _listing_pyeong("청약", {}, {}) is None
