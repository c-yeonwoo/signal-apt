"""단지 비교의 관측 지표를 실제 필요현금·매수 추천으로 오해하지 않는다."""

from realty_signal import api


def test_gap_ranking_requires_same_exclusive_area():
    same = [{"단지명": "A", "갭": 20000, "전용㎡": 84.9},
            {"단지명": "B", "갭": 15000, "전용㎡": 84.7}]
    ranked, used = api._compare_score(["전세차액"], same)
    assert used == 1 and ranked[0]["단지명"] == "B"

    different = [same[0], {**same[1], "전용㎡": 59.8}]
    _, used = api._compare_score(["전세차액"], different)
    assert used == 0
    assert "면적" in api._compare_rule("실투자금", different)


def test_gap_explanation_names_missing_costs():
    same = [{"단지명": "A", "갭": 20000, "전용㎡": 84.9},
            {"단지명": "B", "갭": 15000, "전용㎡": 84.7}]
    assert "필요현금은 아닙니다" in api._compare_rule("실투자금", same)


def test_verified_comparison_omits_old_trade_gap(monkeypatch):
    def detail(_region, name):
        status = "최근거래없음" if name == "오래된단지" else "표본적음"
        return {"단지명": name, "평형별": [{"전용㎡": 84.9, "매매건수": 3,
                                         "갭": 25000, "비교거래": {"상태": status}}]}

    monkeypatch.setattr(api, "complex_detail", detail)
    out = api._verified_comparison({"complexes": [
        {"region": "노원구", "단지명": "오래된단지"},
        {"region": "노원구", "단지명": "최근단지"},
    ]})
    assert out[0]["갭"] is None and out[1]["갭"] == 25000
