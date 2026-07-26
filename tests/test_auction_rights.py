"""경매 권리분석 — 말소기준 판정, 대항력, 인수금액."""

from __future__ import annotations

from realty_signal import auction_rights as ar

MORTGAGE = {"종류": "근저당권", "일자": "2019-03-05", "금액": 24000, "권리자": "국민은행"}


def test_baseline_is_earliest_eligible_right():
    a = ar.analyze([
        {"종류": "가압류", "일자": "2021-01-10"},
        MORTGAGE,
        {"종류": "경매개시결정", "일자": "2024-06-01"},
    ], [])
    assert a["말소기준"]["종류"] == "근저당권"
    assert a["말소기준"]["일자"] == "2019-03-05"


def test_non_baseline_right_never_becomes_baseline():
    a = ar.analyze([
        {"종류": "지상권", "일자": "2015-01-01"},
        MORTGAGE,
    ], [])
    assert a["말소기준"]["종류"] == "근저당권"


def test_junior_rights_extinguish_senior_rights_are_assumed():
    a = ar.analyze([
        MORTGAGE,
        {"종류": "지상권", "일자": "2015-01-01"},
        {"종류": "가처분", "일자": "2022-08-08"},
    ], [])
    by = {r["종류"]: r for r in a["권리"]}
    assert by["지상권"]["판정"] == "인수"
    assert by["가처분"]["판정"] == "소멸"
    assert by["근저당권"]["판정"] == "소멸"
    assert a["등급"] == "위험"


def test_lien_is_always_flagged():
    a = ar.analyze([MORTGAGE, {"종류": "유치권", "일자": "2023-01-01"}], [])
    lien = next(r for r in a["권리"] if r["종류"] == "유치권")
    assert lien["판정"] == "확인필요"
    assert any("유치권" in w for w in a["위험"])


def test_tenant_without_opposing_power_is_not_assumed():
    a = ar.analyze([MORTGAGE], [
        {"전입일": "2021-05-01", "확정일자": "2021-05-01", "보증금": 30000, "배당요구": True},
    ])
    t = a["임차인"][0]
    assert t["대항력"] is False and t["판정"] == "소멸" and t["인수금액"] == 0
    assert a["인수합계"] == 0 and a["등급"] == "안전"


def test_senior_tenant_without_distribution_claim_is_fully_assumed():
    a = ar.analyze([MORTGAGE], [
        {"이름": "홍길동", "전입일": "2018-01-02", "보증금": 30000, "배당요구": False},
    ])
    t = a["임차인"][0]
    assert t["대항력"] is True and t["판정"] == "인수" and t["인수금액"] == 30000
    assert a["인수합계"] == 30000
    assert "30,000만 인수" in " ".join(a["위험"])


def test_senior_tenant_without_fixed_date_is_treated_as_assumed():
    a = ar.analyze([MORTGAGE], [
        {"전입일": "2018-01-02", "보증금": 20000, "배당요구": True, "확정일자": None},
    ])
    assert a["임차인"][0]["판정"] == "확인필요"
    assert a["인수합계"] == 20000       # 안전한 쪽으로 잡는다


def test_senior_tenant_with_fixed_date_needs_distribution_check():
    a = ar.analyze([MORTGAGE], [
        {"전입일": "2018-01-02", "확정일자": "2018-01-02", "보증금": 20000, "배당요구": True},
    ])
    t = a["임차인"][0]
    assert t["대항력"] is True and t["판정"] == "확인필요" and t["인수금액"] == 0
    assert a["등급"] == "주의"


def test_same_day_move_in_has_no_opposing_power():
    """대항력은 전입 다음날 0시 — 같은 날이면 근저당이 앞선다."""
    a = ar.analyze([MORTGAGE], [
        {"전입일": "2019-03-05", "보증금": 10000, "배당요구": False},
    ])
    assert a["임차인"][0]["대항력"] is False
    assert a["인수합계"] == 0


def test_no_baseline_is_reported_not_guessed():
    a = ar.analyze([{"종류": "지상권", "일자": "2015-01-01"}], [])
    assert a["말소기준"] is None
    assert a["권리"][0]["판정"] == "확인필요"
    assert any("말소기준권리 없음" in w for w in a["위험"])


def test_senior_jeonse_right_is_flagged_not_decided():
    a = ar.analyze([MORTGAGE, {"종류": "전세권", "일자": "2017-01-01", "금액": 25000}], [])
    j = next(r for r in a["권리"] if r["종류"] == "전세권")
    assert j["판정"] == "확인필요" and "배당요구" in j["사유"]


def test_kind_aliases_and_date_formats():
    a = ar.analyze([{"종류": "근저당", "일자": "20190305"},
                    {"종류": "강제경매개시결정", "일자": "2024.06.01"}], [])
    assert a["말소기준"]["종류"] == "근저당권"
    assert {r["종류"] for r in a["권리"]} == {"근저당권", "경매개시결정"}


def test_conclusion_and_disclaimer_present():
    a = ar.analyze([MORTGAGE], [])
    assert "말소기준" in a["결론"]
    assert a["면책"].startswith("자동 판정은")


def test_small_lease_note_flags_only_below_regional_cap():
    assert "소액임차인" in ar.small_lease_note("서울 노원구", 12000)
    assert ar.small_lease_note("서울 노원구", 50000) is None
    assert ar.small_lease_note("서울 노원구", 0) is None
    assert ar.small_lease_note("천안시", 12000) is None      # 기타 지역 기준 7,500만
    assert "소액임차인" in ar.small_lease_note("부산 해운대구", 8000)
