import pandas as pd

from realty_signal.ingest.kb_weekly import _clean_region, _normalize_dates
from realty_signal.signals.history import diff
from realty_signal.signals.engine import (
    SignalConfig,
    _classify,
    _demand_state,
    _jeonse_state,
    _momentum,
    interpret,
)


def test_clean_region():
    assert _clean_region("전국 Total") == "전국"
    assert _clean_region("6개광역시 6 Large Cities") == "6개광역시"
    assert _clean_region("강남 Southern Seoul") == "강남"


def test_normalize_dates_carries_year_and_rollover():
    from datetime import datetime

    raw = [datetime(2008, 4, 14), "4.21", "4.28", "5.5", "12.29", "1.5"]
    out = _normalize_dates(raw)
    assert out[0] == pd.Timestamp(2008, 4, 14)
    assert out[1] == pd.Timestamp(2008, 4, 21)
    assert out[3] == pd.Timestamp(2008, 5, 5)
    # 12 → 1 로 줄면 연도 증가
    assert out[4] == pd.Timestamp(2008, 12, 29)
    assert out[5] == pd.Timestamp(2009, 1, 5)


def test_jeonse_state_bands():
    c = SignalConfig()
    assert _jeonse_state(90, c) == "공급우위"
    assert _jeonse_state(130, c) == "보통"
    assert _jeonse_state(160, c) == "타이트"
    assert _jeonse_state(180, c) == "전세난"
    assert _jeonse_state(195, c) == "매매전이"


def test_demand_state_ladder():
    c = SignalConfig()
    assert _demand_state(3, c) == "매우약함"
    assert _demand_state(7, c) == "약함"
    assert _demand_state(12, c) == "보통"
    assert _demand_state(17, c) == "강함"
    assert _demand_state(25, c) == "매수신호"


def test_momentum_labels():
    c = SignalConfig()
    assert c.momentum_up == 0.20 and c.momentum_down == -0.20
    up = pd.Series([0.25, 0.30, 0.22, 0.40])
    mild = pd.Series([0.10, 0.08, 0.12, 0.09])  # 옛 0.05% 선에서는 상승이었다
    down = pd.Series([-0.30, -0.25, -0.22, -0.40])
    flat = pd.Series([0.0, 0.01, -0.01, 0.0])
    assert _momentum(up, c)[1] == "상승"
    assert _momentum(mild, c)[1] == "보합"
    assert _momentum(down, c)[1] == "하락"
    assert _momentum(flat, c)[1] == "보합"


def test_classify_chart_priority():
    c = SignalConfig()
    # 차트 3박자: 전세난 + 매수우위지수 강세 + 매매상승
    sig, reasons = _classify(180, 75, 12, "전세난", "상승", c)
    assert sig == "STRONG_BUY"
    assert any("전세난" in r for r in reasons)
    assert any("매수우위지수" in r for r in reasons)
    # 차트 2개 → BUY
    assert _classify(180, 75, 3, "전세난", "보합", c)[0] == "BUY"
    # 차트 1개 → WATCH
    assert _classify(160, 40, 3, "타이트", "보합", c)[0] == "WATCH"
    # 하락 + 매수우위 약함 → SELL_RISK
    assert _classify(90, 30, 2, "공급우위", "하락", c)[0] == "SELL_RISK"


def test_classify_supply_glut():
    c = SignalConfig()
    # 공급과잉 + 매매하락 → SELL_RISK (입주폭탄)
    sig, reasons = _classify(120, 40, 3, "보통", "하락", c, supply=1.8)
    assert sig == "SELL_RISK"
    assert any("공급과잉" in r for r in reasons)
    # 공급과잉 + 매수 시그널 → 유지하되 입주부담 경고
    sig, reasons = _classify(180, 75, 12, "전세난", "상승", c, supply=1.5)
    assert sig == "STRONG_BUY"
    assert any("입주부담" in r for r in reasons)
    # 공급부족 → 근거 표기
    _, reasons = _classify(130, 40, 3, "보통", "보합", c, supply=0.5)
    assert any("공급부족" in r for r in reasons)


def test_classify_memo_is_reference_only():
    c = SignalConfig()
    # 매수세우위 20↑ 여도 차트 조건 없으면 등급은 NEUTRAL, 근거에만 [참고]로 표기
    sig, reasons = _classify(120, 40, 25, "보통", "보합", c)
    assert sig == "NEUTRAL"
    assert any("[참고]" in r and "매수세우위" in r for r in reasons)


def test_history_diff_detects_grade_changes():
    prev = {"as_of": "2026-06-15", "signals": {"서울": "WATCH", "부산": "BUY", "대구": "NEUTRAL"}}
    df = pd.DataFrame([
        {"region": "서울", "signal": "STRONG_BUY", "근거": "x"},   # 상승
        {"region": "부산", "signal": "SELL_RISK", "근거": "y"},     # 하락
        {"region": "대구", "signal": "NEUTRAL", "근거": "z"},       # 변화없음
        {"region": "신규", "signal": "BUY", "근거": "w"},           # 이전 없음 → 무시
    ])
    ch = diff(prev, df)
    regions = {c["region"]: c for c in ch}
    assert set(regions) == {"서울", "부산"}
    assert regions["서울"]["direction"].startswith("▲")
    assert regions["부산"]["direction"].startswith("▼")
    # 변화 큰 순 정렬
    assert ch[0]["region"] in ("서울", "부산")


def test_interpret_narratives():
    c = SignalConfig()
    s1 = interpret("STRONG_BUY", "전세난", 84, "강함", "상승", 0.5, c)
    assert "전세난" in s1 and ("매수" in s1 or "상승" in s1)
    assert s1.endswith(".") and s1.count(".") <= 2
    s2 = interpret("SELL_RISK", "공급우위", 30, "약함", "하락", 1.8, c)
    assert "하락" in s2 or "약세" in s2
    assert "공급" in s2  # 입주 과잉 언급
