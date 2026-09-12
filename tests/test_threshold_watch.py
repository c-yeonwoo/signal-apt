"""임계 근접 워치 — 예측이 아니라 거리.

2026-09-06 조사: 등급이 계단 함수라 시장이 연속적으로 식는 동안 화면은 몇 달 정지했다가
어느 주에 한꺼번에 뒤집힌다. 강남권 매수우위지수가 2.17 움직이자 11개 구가 동시 강등됐고,
지금은 임계에서 0.71 아래다. 그 거리를 보여주는 게 이 기능이다.
"""

from __future__ import annotations

import pathlib
import re

import pandas as pd
import pytest

from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.services import threshold_watch as tw
from realty_signal.signals.engine import SignalConfig


def _kb(bs_last: float, js_last: float = 175.0, n: int = 20) -> KBWeekly:
    """마지막 주의 매수우위/전세수급을 지정한 합성 KB. 잔잔한 변동(±1)을 준다."""
    dates = pd.date_range("2026-04-06", periods=n, freq="W-MON")
    rows = []
    for i, d in enumerate(dates):
        wob = 1.0 if i % 2 else -1.0
        last = i == n - 1
        rows += [
            ("테스트구", d, "buyer_superiority", bs_last if last else bs_last + wob),
            ("테스트구", d, "jeonse_supply", js_last if last else js_last + wob),
            ("테스트구", d, "buyer_demand", 5.0),
            ("테스트구", d, "sale_change", 0.20),
        ]
    long = pd.DataFrame(rows, columns=["region", "date", "metric", "value"])
    return KBWeekly(long=long, codes={})


def test_near_threshold_is_reported_with_distance():
    """임계 바로 아래면 '남은 거리' 와 '몇 주치' 를 함께 낸다."""
    out = tw.compute(_kb(69.3), SignalConfig())
    hit = next(x for x in out["items"] if x["metric"] == "매수우위지수")
    assert hit["need"] == pytest.approx(0.7, abs=0.01)
    assert hit["direction"] == "up"
    assert hit["weekly_move"] > 0 and hit["weeks_away"] > 0
    # 넘었을 때의 등급은 **정본 함수로 다시 분류**한 값이어야 한다
    assert hit["now"] != hit["if_crossed"]


def test_far_from_threshold_is_not_reported():
    """멀면 알리지 않는다 — 매주 울리면 아무도 안 본다."""
    out = tw.compute(_kb(30.0), SignalConfig())
    assert not [x for x in out["items"] if x["metric"] == "매수우위지수"]


def test_crossing_that_does_not_change_grade_is_dropped():
    """임계를 넘어도 등급이 그대로면 알릴 가치가 없다."""
    out = tw.compute(_kb(69.3, js_last=120.0), SignalConfig())
    for x in out["items"]:
        assert x["now"] != x["if_crossed"]


def test_inherited_metrics_are_grouped_by_publication_unit():
    """광역 공통 지표를 구별로 늘어놓으면 12줄이 같은 값을 반복한다.

    `weekly._movers` 와 같은 규칙 — 발표 단위로 묶고 `regions`·`members` 를 남긴다.
    """
    from realty_signal.services import market_data as md

    out = tw.compute(md.kb(), md.signal_config())
    for x in out["items"]:
        assert "regions" in x and "members" in x
        assert len(x["members"]) == x["regions"]
        if x["regions"] > 1:
            assert x["shared"] is True, "묶였는데 상속 표시가 없다"
    # 같은 (발표단위, 지표) 가 두 줄로 나오면 묶기가 깨진 것이다
    keys = [(x["scope"], x["metric"]) for x in out["items"]]
    assert len(keys) == len(set(keys))


def test_response_carries_a_not_a_prediction_note():
    """이 카드가 무엇이 **아닌지** 화면이 말해야 한다 — 반가치(확신형 예측) 경계."""
    out = tw.compute(_kb(69.3), SignalConfig())
    assert "예측" in out["note"] and "거리" in out["note"]


def test_no_predictive_wording_in_frontend():
    """화면 문구에 확신형 예측이 섞이면 안 된다."""
    html = pathlib.Path("src/realty_signal/web/index.html").read_text(encoding="utf-8")
    m = re.search(r"async function _loadNear\(\)\{.*?\n\}", html, re.S)
    assert m, "_loadNear 미발견"
    body = m.group(0)
    for banned in ("오릅니다", "떨어집니다", "기회", "확실", "예상됩니다"):
        assert banned not in body, f"예측성 문구 '{banned}' 가 들어갔다"
    assert "남음" in body and "주치" in body


def test_cache_version_exists():
    """스키마가 바뀌면 옛 캐시가 조용히 살아남아 ★ 가 안 붙는 식으로 어긋난다."""
    assert isinstance(tw.CACHE_VER, int)
    home = pathlib.Path("src/realty_signal/routes/home.py").read_text(encoding="utf-8")
    assert "tw.CACHE_VER" in home, "캐시 키에 버전이 없다"


def test_per_user_star_is_applied_after_cache():
    """캐시는 사용자와 무관하다 — ★ 는 `members` 로 나중에 입힌다."""
    home = pathlib.Path("src/realty_signal/routes/home.py").read_text(encoding="utf-8")
    m = re.search(r"def threshold_watch\(request: Request\):.*?(?=\n@router|\ndef )", home, re.S)
    assert m
    src = m.group(0)
    assert 'x.get("members")' in src, "members 로 ★ 를 판정하지 않는다"
