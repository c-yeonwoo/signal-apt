"""개인 데이터 레이어·다이제스트 확장 단위 테스트."""

from __future__ import annotations

from realty_signal import personal_layer as pl
from realty_signal.digest import build_user_digest


def test_volume_summary_shape():
    s = pl.volume_summary("종로구")
    assert "거래량비" in s and "spark" in s


def test_macro_latest_optional():
    m = pl.macro_latest()
    assert isinstance(m, dict)


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


def test_digest_includes_extras():
    d = build_user_digest(
        "a@b.com", ["마포구"], [], {"마포구": "BUY"}, "2026-07-14",
        extras={
            "macro": {"대출금리": 3.5, "구매력": 120, "기준": "2026-07"},
            "volumes": {"마포구": 1.2},
            "complexes": [{"name": "공덕래미안", "전세가율": 70, "갭": 20000}],
        },
    )
    assert "거시" in d["body"] and "거래량비" in d["body"] and "전세가율" in d["body"]


def test_nbhd_metrics_includes_volume():
    from realty_signal.api import _nbhd_metrics
    m = _nbhd_metrics({"시그널": "BUY", "매물": {"급매": 1}, "거래량": {"거래량비": 1.3},
                       "국면": {"phase": "회복"}})
    assert m["거래량비"] == 1.3
