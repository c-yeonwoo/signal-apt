"""규제 테이블 — 규제지역 판정·LTV·절대한도·스트레스금리."""

from __future__ import annotations

import pytest

from realty_signal import regulation as reg


def test_seoul_all_gu_regulated():
    assert len(reg.SEOUL_GU) == 25
    for gu in ("강남구", "노원구", "금천구"):
        assert reg.is_regulated(gu)
        assert reg.is_regulated(f"서울 {gu}")


def test_ambiguous_gu_needs_sido():
    # 서울 중구는 규제, 부산 중구는 아니다
    assert reg.is_regulated("중구", "서울")
    assert not reg.is_regulated("중구", "부산")
    assert not reg.is_regulated("중구")          # 시도 미상이면 규제로 단정하지 않는다
    assert reg.is_regulated("서울 강서구")
    assert not reg.is_regulated("강서구", "부산")


def test_gyeonggi_regulated_list():
    assert len(reg.REGULATED_GYEONGGI) == 15     # 10·15 12곳 + 2026.7.1 3곳
    assert reg.is_regulated("성남시 분당구")
    assert reg.is_regulated("용인시 기흥구")
    assert not reg.is_regulated("용인시 처인구")
    assert not reg.is_regulated("고양시 덕양구")


def test_metro_covers_non_regulated_sudogwon():
    assert reg.is_metro("남양주시", "경기")
    assert reg.is_metro("연수구", "인천")
    assert not reg.is_metro("해운대구", "부산")
    assert reg.is_metro("강남구")                # 규제지역이면 시도 없이도 수도권


@pytest.mark.parametrize("price,cap", [
    (100_000, 60_000), (150_000, 60_000), (150_001, 40_000),
    (250_000, 40_000), (300_000, 20_000),
])
def test_loan_cap_brackets(price, cap):
    assert reg.loan_cap(price, metro=True, regulated=False) == cap


def test_loan_cap_only_in_metro():
    assert reg.loan_cap(300_000, metro=False, regulated=False) == float("inf")


def test_tier_selection():
    assert reg.tier(0, first_time=True, dispose=True, income=30_000, price=300_000) == "생애최초"
    assert reg.tier(0, first_time=False, dispose=True, income=8_000, price=70_000) == "서민실수요"
    assert reg.tier(0, first_time=False, dispose=True, income=8_000, price=90_000) == "무주택"
    assert reg.tier(1, first_time=False, dispose=True, income=None, price=0) == "1주택_처분조건"
    assert reg.tier(1, first_time=False, dispose=False, income=None, price=0) == "1주택_유지"
    assert reg.tier(3, first_time=False, dispose=True, income=None, price=0) == "다주택"


def test_first_time_ltv_is_price_and_income_blind():
    """생애최초 우대는 지역·주택가격·소득 무관(2022.8 감독규정). 비율만 축소됐다."""
    for price in (50_000, 300_000):
        assert reg.tier(0, first_time=True, dispose=True, income=50_000, price=price) == "생애최초"
    assert reg.ltv_of("생애최초", True) == 0.70
    assert reg.ltv_of("생애최초", False) == 0.80


def test_stress_rate_by_region_and_rate_type():
    assert reg.stress_rate(metro=True, regulated=True, rate_type="변동") == pytest.approx(0.03)
    assert reg.stress_rate(metro=True, regulated=False, rate_type="주기") == pytest.approx(0.009)
    assert reg.stress_rate(metro=True, regulated=False, rate_type="고정") == 0.0
    assert reg.stress_rate(metro=False, regulated=False, rate_type="변동") == pytest.approx(0.0075)


def test_max_years_clamped_in_metro():
    assert reg.max_years(40, metro=True, regulated=False) == 30
    assert reg.max_years(40, metro=False, regulated=False) == 40
    assert reg.max_years(20, metro=True, regulated=True) == 20
