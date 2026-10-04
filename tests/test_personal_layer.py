"""개인 데이터 레이어 단위 테스트."""

from __future__ import annotations

from realty_signal import personal_layer as pl


def test_volume_summary_shape():
    s = pl.volume_summary("종로구")
    assert "거래량비" in s and "spark" in s


def test_macro_latest_optional():
    m = pl.macro_latest()
    assert isinstance(m, dict)


def test_kb_affordability_trend_increases_with_index():
    from realty_signal.signals.engine import macro_trend

    result = macro_trend({"구매력": [95, 96, 97, 98, 99, 100, 101]})
    assert result["power_dir"] == "상승"


def test_locality_bits_empty():
    assert pl.locality_bits({}) == {}
    assert pl.locality_bits({"school": 10, "transit_min": 40})["학원밀도"] == 10


def test_ext_links_has_safemap():
    links = pl.ext_links("마포구")
    assert "생활안전지도" in links and "에어코리아" in links


def test_loan_scenarios_three_ltvs():
    def fake(capital, ltv, income, rate, years=30):
        p = int(capital / (1 - ltv))
        return p, {"대출": int(p * ltv), "취득세": 0, "중개비": 0, "자기자본": capital, "DSR제약": False}
    rows = pl.loan_scenarios(30000, 5000, 0.04, 30, fake)
    assert [r["ltv"] for r in rows] == [60, 70, 80]
    assert rows[0]["매수가능가"] < rows[2]["매수가능가"]


def test_nbhd_metrics_includes_volume():
    from realty_signal.api import _nbhd_metrics
    m = _nbhd_metrics({"시그널": "BUY", "매물": {"급매": 1}, "거래량": {"거래량비": 1.3},
                       "국면": {"phase": "회복"}})
    assert m["거래량비"] == 1.3


def test_gongsi_samples_use_verified_complex_code_only(monkeypatch):
    from realty_signal import db

    monkeypatch.setattr(db, "fav_list", lambda uid: [
        {"kind": "complex", "key": "중구|옛 단지"},
        {"kind": "complex", "key": "kb:1114000000|서울 단지"}])
    monkeypatch.setattr(db, "complex_favorite_region", lambda ref: (
        {"status": "ready", "name": "중구", "code": "1114000000"}
        if ref.startswith("kb:") else {"status": "needs_reselection"}))
    cache_keys = []
    def cached(key, **kw):
        cache_keys.append(key)
        return {"㎡단가": 1000000} if key.startswith("gongsi:") else {"최근평단가": 4000}
    monkeypatch.setattr(db, "kv_get", cached)
    rows = pl.fav_gongsi_samples(1, "중구")
    assert rows[0]["단지명"] == "서울 단지"
    assert cache_keys == ["gongsi:중구:서울 단지", "complex:11140:서울 단지"]
