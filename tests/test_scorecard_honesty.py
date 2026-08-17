"""시그널 성적표는 base rate 와 함께 나가야 한다.

적중률만 내보내면 무정보일 수 있다 — "항상 매수"라고만 답하는 예측기도 상승장에선
높은 적중률을 얻는다. 그래서 같은 날짜 분포에서 전체 지역이 얻은 값(`시장평균`)과
그 차이(`초과`)를 항상 함께 낸다. 이 테스트가 그 계약을 고정한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from realty_signal.api import app
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.signals.engine import SignalConfig, backtest_summary


def _synthetic_kb(n_regions: int = 6, n_weeks: int = 260) -> KBWeekly:
    """결정론적 합성 시계열. 네트워크·캐시 파일에 의존하지 않는다."""
    dates = pd.date_range("2019-01-07", periods=n_weeks, freq="W-MON")
    rng = np.random.default_rng(20260817)
    rows = []
    for i in range(n_regions):
        region = f"테스트{i}구"
        # 지역마다 위상이 다른 사인파 + 약한 잡음 → 상승·하락 국면이 번갈아 나온다
        phase = i * 0.7
        for k, d in enumerate(dates):
            wave = np.sin(k / 26 * np.pi + phase)
            rows.append((region, d, "sale_change", float(wave * 0.3 + rng.normal(0, 0.05))))
            rows.append((region, d, "jeonse_supply", float(120 + wave * 55)))
            rows.append((region, d, "buyer_superiority", float(60 + wave * 45)))
            rows.append((region, d, "buyer_demand", float(10 + wave * 12)))
    long = pd.DataFrame(rows, columns=["region", "date", "metric", "value"])
    return KBWeekly(long=long, codes={})


def test_backtest_reports_base_rate_and_lift():
    bt = backtest_summary(_synthetic_kb(), SignalConfig())
    assert bt["by_signal"], "합성 데이터에서 시그널 구간이 하나도 안 나왔다"
    for s in bt["by_signal"]:
        assert "시장평균" in s, f"{s['signal']}: base rate 가 빠졌다"
        assert "초과" in s, f"{s['signal']}: 리프트가 빠졌다"
        if s["적중률"] is not None and s["시장평균"] is not None:
            # 초과는 반드시 두 값의 차이여야 한다 (따로 노는 숫자면 안 된다)
            assert abs(s["초과"] - round(s["적중률"] - s["시장평균"], 1)) < 0.05
            assert 0 <= s["시장평균"] <= 100


def test_backtest_reports_sample_caveats():
    """평가수가 독립 관측 수가 아니라는 사실을 데이터로 전달해야 한다."""
    bt = backtest_summary(_synthetic_kb(), SignalConfig())
    smp = bt.get("표본") or {}
    assert smp.get("지역수") == 6
    assert "원본지표보유" in smp and "광역상속" in smp
    assert smp["원본지표보유"] + smp["광역상속"] == smp["지역수"]
    assert len(bt.get("주의") or []) >= 3, "주의 문구가 화면에 나갈 만큼 갖춰지지 않았다"


def test_backtest_is_public_but_other_apis_are_not():
    """가입 전에 증명을 볼 수 있어야 한다. 단 공개는 집계 성적표 하나뿐이다."""
    c = TestClient(app)
    assert c.get("/api/backtest").status_code == 200
    for ep in ("/api/signals", "/api/regions", "/api/conclusion", "/api/shortlist"):
        assert c.get(ep).status_code == 401, f"{ep} 가 비로그인에 열려 있다"


def test_public_backtest_has_no_region_level_data():
    """공개 응답에 지역별·개인별 데이터가 섞여 나가면 안 된다."""
    body = TestClient(app).get("/api/backtest").json()
    assert set(body) <= {"기준일", "by_signal", "표본", "주의", "설명", "data_age_days"}
    for s in body["by_signal"]:
        assert "region" not in s and "지역" not in s
