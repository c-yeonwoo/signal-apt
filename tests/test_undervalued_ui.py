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
    assert "싼 편" in locality._interpret_locality(_row(30))
    assert "비싼 편" in locality._interpret_locality(_row(-30))
    assert "걸맞은 시세" in locality._interpret_locality(_row(1))


def test_extreme_values_are_not_stated_as_a_plain_percentage():
    """|저평가도|가 100 을 넘으면 모델이 설명 못 하는 구간 — 숫자로 단정하면 안 된다.

    과천·강남은 −300% 가 나오는데 이걸 '300% 비쌈'으로 쓰면 브랜드·재건축 프리미엄이
    미반영이라는 사실이 숫자에 가려진다.
    """
    txt = locality._interpret_locality(_row(-303.3))
    assert "프리미엄" in txt
    assert "303" not in txt


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_map_and_chart_sit_side_by_side(html):
    """지도와 산점도는 같은 지역을 다르게 보여주므로 나란히 둔다(좁은 화면에서만 상하)."""
    assert ".uvsplit { display:flex" in html
    assert html.index('class="uvsplit"') < html.index('id="uvchart"')
    assert ".uvsplit { flex-direction:column" in html   # 모바일 스택


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
