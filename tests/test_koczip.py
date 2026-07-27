"""콕집 클라이언트 헬퍼 (네트워크 호출 없음)."""

from realty_signal.ingest import koczip as kz


def test_buyer_discount_pct_from_asking_below_real():
    # discount_min = (호가min − 실거래평균) / 실거래평균
    assert kz.buyer_discount_pct({"discount_min": -0.269}) == 26.9
    assert kz.buyer_discount_pct({"discount_min": 0.1}) == -10.0
    assert kz.buyer_discount_pct({}) is None


def test_region_candidates_seoul_gu():
    c = kz.region_candidates("서울시 노원구 상계동")
    assert "노원구" in c
    assert "서울시 노원구" in c


def test_region_candidates_suwon():
    c = kz.region_candidates("경기도 수원시 권선구 세류동")
    assert "수원시 권선구" in c
    assert "권선구" in c
