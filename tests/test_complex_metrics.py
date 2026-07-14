"""단지 전세가율·갭 추출 및 단지시그널 가중(전세 유무) 단위 테스트."""

from __future__ import annotations

from realty_signal.api import _complex_signal, _main_flat_metrics


def test_main_flat_metrics_picks_busiest_pyeong():
    d = {
        "평형별": [
            {"평형": 24, "매매건수": 2, "전세가율": 55, "갭": 20000},
            {"평형": 34, "매매건수": 10, "전세가율": 72, "갭": 15000, "최근매매": 80000},
        ],
        "매매추이": [{"평단가": 100}, {"평단가": 110}, {"평단가": 105}],
    }
    m = _main_flat_metrics(d)
    assert m["주력평형"] == 34
    assert m["전세가율"] == 72
    assert m["갭"] == 15000
    assert m["spark"] == [100, 110, 105]


def test_main_flat_metrics_missing_jeonse():
    m = _main_flat_metrics({"평형별": [{"평형": 30, "매매건수": 3}], "매매추이": []})
    assert m["전세가율"] is None and m["갭"] is None
    assert m["spark"] == []


def test_complex_signal_reduces_comp_weight_without_jeonse(monkeypatch):
    monkeypatch.setattr(
        "realty_signal.api._regime",
        lambda: {"regions": {"마포구": {}}, "endgame": False},
    )
    monkeypatch.setattr("realty_signal.api._uv_map", lambda: {})
    base = {
        "추세pct": 0,
        "총거래": 24,
        "매매추이": [{"평단가": 100}, {"평단가": 100}, {"평단가": 100}],
        "평형별": [{"평형": 30, "매매건수": 5, "전세가율": 75}],
    }
    with_j = _complex_signal("마포구", base, "BUY", None)
    no_j = _complex_signal(
        "마포구",
        {**base, "평형별": [{"평형": 30, "매매건수": 5}]},
        "BUY",
        None,
    )
    assert "근거부족" not in with_j
    assert no_j.get("근거부족") == ["전세가율"]
    assert "주의" in no_j
    # 전세 높으면 단지 성분·총점이 더 우호적
    assert with_j["분해"]["단지"] >= no_j["분해"]["단지"]
    assert with_j["점수"] >= no_j["점수"]
