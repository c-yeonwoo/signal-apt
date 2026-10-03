"""A generic or other-district policy must not become a local investment catalyst."""

from realty_signal import api, db


def test_neighborhood_development_only_uses_explicit_district_scope():
    db.policy_add("수도권 광역 계획", "광역 참고", "개발계획", "수도권", source="기관 A")
    db.policy_add("다른 동네 사업", "무관", "개발계획", "강남구", source="기관 B")
    db.policy_add("전국 정비 제도", "제도 참고", "정비사업", "전국", source="기관 C")
    assert api._local_development_docs("노원구") == []

    db.policy_add("노원구 사업 후보", "단계 미확인", "개발계획", "노원구",
                  source="기관 D", eff_date="확인요망")
    assert api._local_development_docs("노원구") == [
        {"title": "노원구 사업 후보", "source": "기관 D", "eff_date": "확인요망"}]


def test_policy_search_does_not_fall_back_to_unrelated_recent_docs():
    db.policy_add("타 지역 공급", "부천시 택지", "개발계획", "부천시")
    db.policy_add("전국 대출 제도", "DSR", "대출규제", "전국")
    assert db.policy_search("노원구 공급", region="노원구") == []
    assert db.policy_search("DSR", region="노원구")[0]["region"] == "전국"
    assert db.policy_search("공급", region="노원구") == []
