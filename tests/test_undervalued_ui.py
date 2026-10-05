"""저평가 화면 — 초보자 표현 규칙과 드릴다운 배선 회귀 방지.

숫자 계산은 test_locality.py 가 본다. 여기서는 '사람이 읽을 수 있게 나가는가'만 본다.
"""
from pathlib import Path

import pytest

from realty_signal.ingest import locality

INDEX = Path(__file__).resolve().parents[1] / "src/realty_signal/web/index.html"


def _row(uv: float, price: int = 3000) -> dict:
    return {"_acc": 70, "_sch": 50, "_env": 60, "transit_min": 40, "최단업무지구": "강남",
            "저평가도": uv, "price": price, "적정가": round(price / (1 - uv / 100))}


@pytest.mark.parametrize("uv", [63.6, 25, 12, 3, -3, -12, -47, -160, -303.3])
def test_haesol_never_says_negative_undervalued(uv):
    """'저평가 -47%' 같은 이중부정은 아무도 못 읽는다 — 방향은 항상 단어로."""
    txt = locality._interpret_locality(_row(uv))
    assert "저평가 -" not in txt and "저평가도 -" not in txt
    assert "-" not in txt.split(". ")[-1], txt   # 가격 판정 문장에 음수 기호가 남지 않는다


def test_haesol_direction_matches_sign():
    assert "모형값보다 많이 낮습니다" in locality._interpret_locality(_row(30))
    assert "모형값보다 많이 높습니다" in locality._interpret_locality(_row(-30))
    assert "모형값 근처" in locality._interpret_locality(_row(1))


def test_extreme_values_are_not_stated_as_a_plain_percentage():
    """|저평가도|가 100 을 넘으면 모델이 설명 못 하는 구간 — 숫자로 단정하면 안 된다.

    과천·강남은 −300% 가 나오는데 이걸 '300% 비쌈'으로 쓰면 브랜드·재건축 프리미엄이
    미반영이라는 사실이 숫자에 가려진다.
    """
    txt = locality._interpret_locality(_row(-303.3))
    assert "모형 설명 범위 밖" in txt
    assert "303" not in txt


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_region_price_view_uses_observed_trades_in_matching_area_bands(html):
    assert 'id="rpcArea"' in html and 'id="rpcRegionA"' in html and 'id="rpcRegionB"' in html
    assert '/api/region-trade-prices/' in html
    assert 'id="rpcCards"' in html and 'class="rpc-grid"' in html
    assert '.rpc-grid { grid-template-columns:minmax(0,1fr); }' in html
    assert '국토교통부 아파트 매매 실거래' in html
    assert '지역·단지의 적정가나 매수 신호는 아닙니다' in html


def test_grade_groups_collapse_and_drill_down_to_complexes(html):
    assert "function toggleUvGrade(" in html
    assert "async function expandUvComplexes(" in html
    assert "'/api/complex-grades/'+encodeURIComponent(region)" in html


def test_undervalued_wording_goes_through_one_helper(html):
    """저평가도를 화면에 직접 %로 찍는 경로가 남아 있으면 표현이 다시 갈라진다."""
    assert "function uvVerdict(" in html
    for legacy in ("저평가 ${uv", "저평가도 ${uv", "저평가+${c['저평가도']}"):
        assert legacy not in html, legacy


def test_rows_show_a_price_a_beginner_can_picture(html):
    """평당가만 주면 초보자는 총액을 모른다 — 34평 환산가를 같이 낸다."""
    assert "34평 기준" in html
    assert "const _UV_PYEONG = 25.7" in html


def test_locality_ui_does_not_relabel_proxy_as_school_district_or_fair_price(html):
    assert "market:   ['signal','undervalued']" in html
    assert "undervalued:{l:'지역 가격 비교'" in html
    assert "교육업종 점포·환경 대리변수" in html
    assert "단지·층·연식·수리 상태·입지와 거래량 차이는 통제하지 않습니다" in html
    assert "모형보다 ${a}% 낮음" in html
    assert "실제 저가 순위 아님" in html
    assert "교통·학군·환경을 0~100으로 점수화" not in html


def test_unlicensed_locality_model_is_not_region_price_default(html):
    assert "undervalued:()=>loadUndervalued()" in html
    assert "async function loadLegacyUndervalued()" in html
    assert "async function loadUndervalued()" in html
    assert html.index("async function loadUndervalued()") < html.index("async function loadLegacyUndervalued()")
