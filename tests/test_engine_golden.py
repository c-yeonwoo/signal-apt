"""엔진 골든 스냅샷 — 임계값을 바꾸면 반드시 무언가 깨지게 한다.

2026-08-17 진단: `buyeridx_strong` 을 70→65 로 바꿔도 38개 테스트가 전부 통과했다.
기존 `test_engine.py` 는 손으로 고른 예시 4~5개만 확인해서, 등급 경계가 통째로 움직여도
아무도 몰랐다. 여기서는 **입력 격자 전체의 등급 분포**를 고정한다.

실제 KB 캐시에 의존하지 않는다(CI·클린 체크아웃에서도 돌아야 하므로).
합성 격자를 `_classify` 에 통과시켜 등급 지도를 만들고 그걸 스냅샷으로 잡는다.
"""

from __future__ import annotations

import pytest

from realty_signal.signals.engine import SignalConfig, _classify, _jeonse_state

# 격자 — 각 축의 경계 안팎을 고루 덮는다
_JEONSE = [90, 120, 145, 168, 172, 188, 195]
_BUYERIDX = [30, 48, 52, 68, 72, 85]
_MOMENTUM = ["상승", "보합", "하락"]
_DEMAND = [3, 12, 22]          # 등급에 영향이 없어야 한다(진단 결과) — 그것도 함께 고정


def _grade(js: float, bs: float, mom: str, bd: float, c: SignalConfig) -> str:
    sig, _ = _classify(js, bs, bd, _jeonse_state(js, c), mom, c)
    return sig


def _grade_map(c: SignalConfig) -> dict[tuple, str]:
    return {
        (js, bs, mom): _grade(js, bs, mom, 3, c)
        for js in _JEONSE for bs in _BUYERIDX for mom in _MOMENTUM
    }


def test_grade_distribution_is_pinned():
    """기본 설정에서의 등급 분포. 임계값·등급식을 건드리면 여기서 먼저 깨진다."""
    gm = _grade_map(SignalConfig())
    counts: dict[str, int] = {}
    for g in gm.values():
        counts[g] = counts.get(g, 0) + 1
    # 실측값(2026-08-17, SignalConfig 기본). 추측이 아니라 격자를 돌려 얻은 값이다.
    assert counts == {
        "STRONG_BUY": 6,
        "BUY": 32,
        "WATCH": 72,
        "NEUTRAL": 12,
        "SELL_RISK": 4,
    }, f"등급 분포가 바뀌었다: {counts}"
    assert len(gm) == len(_JEONSE) * len(_BUYERIDX) * len(_MOMENTUM)


@pytest.mark.parametrize(
    "js,bs,mom,expected",
    [
        # 3표 모두 — 전세난 + 매수우위지수 강세 + 상승
        (172, 72, "상승", "STRONG_BUY"),
        (195, 85, "상승", "STRONG_BUY"),
        # 2표
        (172, 72, "보합", "BUY"),
        (172, 48, "상승", "BUY"),
        (120, 72, "상승", "BUY"),
        # 1표 또는 타이트
        (172, 48, "보합", "WATCH"),
        (145, 30, "보합", "WATCH"),      # 타이트 밴드
        (90, 72, "보합", "WATCH"),
        # 하락 + 매수우위지수 중립 미만
        (90, 30, "하락", "SELL_RISK"),
        (120, 48, "하락", "SELL_RISK"),
        # 하락이지만 매수우위지수가 중립 이상이면 SELL_RISK 아님
        (120, 52, "하락", "NEUTRAL"),
        (90, 30, "보합", "NEUTRAL"),
    ],
)
def test_grade_boundaries(js, bs, mom, expected):
    assert _grade(js, bs, mom, 3, SignalConfig()) == expected


def test_threshold_change_actually_moves_grades():
    """임계값을 바꾸면 등급 지도가 **실제로** 달라져야 한다.

    이 테스트가 통과하는데 나머지가 다 통과한다면, 그건 나머지가 등급을 안 보고 있다는 뜻이다.
    """
    base = _grade_map(SignalConfig())
    loosened = _grade_map(SignalConfig(buyeridx_strong=65))
    diff = {k for k in base if base[k] != loosened[k]}
    assert diff, "buyeridx_strong 70→65 가 등급을 하나도 안 바꿨다 — 등급식이 의심스럽다"
    # 완화했으니 STRONG_BUY 가 늘어야 한다
    assert sum(1 for v in loosened.values() if v == "STRONG_BUY") > \
           sum(1 for v in base.values() if v == "STRONG_BUY")


def test_demand_ladder_does_not_affect_grades():
    """매수세우위(raw)는 등급을 바꾸지 않는다 — 근거 문자열 전용.

    문서가 이걸 '트리거'라고 적어 뒀었고, 그 오해로 `calibrate` 가 헛돌았다(N5·N6).
    사다리를 등급에 되살릴 거라면 이 테스트를 **의도적으로** 갱신하라.
    """
    c = SignalConfig()
    for js in _JEONSE:
        for bs in _BUYERIDX:
            for mom in _MOMENTUM:
                grades = {_grade(js, bs, mom, bd, c) for bd in _DEMAND}
                assert len(grades) == 1, (
                    f"매수세우위가 등급을 바꿨다 js={js} bs={bs} mom={mom} → {grades}"
                )


def test_demand_ladder_still_appears_in_reasons():
    """등급엔 안 쓰이지만 근거 문구로는 계속 나와야 한다(사용자에게 보이는 맥락)."""
    c = SignalConfig()
    _, reasons = _classify(172, 72, 25, _jeonse_state(172, c), "상승", c)
    assert any("[참고] 매수세우위" in r for r in reasons)
    _, reasons_low = _classify(172, 72, 1, _jeonse_state(172, c), "상승", c)
    assert not any("[참고] 매수세우위" in r for r in reasons_low)


def test_supply_glut_downgrades_only_with_falling_prices():
    """입주물량 보정 — 공급과잉 + 하락일 때만 SELL_RISK 로 덮어쓴다."""
    c = SignalConfig()
    sig, reasons = _classify(172, 72, 3, _jeonse_state(172, c), "하락", c, 1.5)
    assert sig == "SELL_RISK"
    assert any("공급과잉" in r for r in reasons)
    # 상승 중이면 등급은 유지하고 주의만 붙는다
    sig2, reasons2 = _classify(172, 72, 3, _jeonse_state(172, c), "상승", c, 1.5)
    assert sig2 == "STRONG_BUY"
    assert any("입주부담" in r for r in reasons2)
