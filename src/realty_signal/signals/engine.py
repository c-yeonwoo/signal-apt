"""시그널 엔진 — KB 지표 임계값 룰을 지역별 신호로 변환.

원본 엑셀에 담긴 투자 룰을 코드화한다:

전세수급지수 (0~200)
  <100  공급우위      : 전세 공급 과잉
  100~140 보통
  140~170 타이트       : 전세 매물 마름
  170~190 전세난        : "어쩔 수 없이 매매로 넘어온다"
  >190  매매전이        : 전세난이 매매 상승 압력으로 전이

매수우위지수 (0~200, 100=중립)
  KB 표준 해석: >100 매수자 우위, <100 매도자 우위.
  ※ 원본 메모의 "5/10/15/20 → 20↑ 매수" 사다리는 스케일이 모호해
    THRESHOLDS 로 분리해 두었고 기본값은 KB 표준을 따른다. (사용자 확인 필요)

매매/전세 증감률 (%)
  최근 N주 평균으로 모멘텀(상승/둔화/하락) 판정.

매도·끝물 시그널(입주물량↑, 상급지→하급지 유동성, 신축→구축 전이)은
입주물량/시장강도 데이터가 필요하므로 여기서는 '교차지역 상승폭 역전'
프록시만 제공한다(부동산지인/아실 연동 시 보강).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from realty_signal.ingest.kb_weekly import KBWeekly


@dataclass
class SignalConfig:
    # 전세수급지수 밴드 경계
    jeonse_oversupply: float = 100.0
    jeonse_tight: float = 140.0
    jeonse_crunch: float = 170.0
    jeonse_spillover: float = 190.0
    # 매수세우위(raw) 사다리 — 원본 메모 "5/10/15/20, 20↑ 매수". KB 실측 median≈3~5.
    demand_l1: float = 5.0   # 약함
    demand_l2: float = 10.0  # 보통
    demand_l3: float = 15.0  # 강함
    demand_buy: float = 20.0  # 매수신호 ("빨리 사야한다")
    # 모멘텀 산정 주(week) 수, 증감률 임계(%)
    momentum_weeks: int = 4
    momentum_up: float = 0.05
    momentum_down: float = -0.05


def _jeonse_state(v: float, c: SignalConfig) -> str:
    if v < c.jeonse_oversupply:
        return "공급우위"
    if v < c.jeonse_tight:
        return "보통"
    if v < c.jeonse_crunch:
        return "타이트"
    if v < c.jeonse_spillover:
        return "전세난"
    return "매매전이"


def _demand_state(v: float, c: SignalConfig) -> str:
    """매수세우위(raw) 사다리 라벨."""
    if v >= c.demand_buy:
        return "매수신호"
    if v >= c.demand_l3:
        return "강함"
    if v >= c.demand_l2:
        return "보통"
    if v >= c.demand_l1:
        return "약함"
    return "매우약함"


def _momentum(series: pd.Series, c: SignalConfig) -> tuple[float, str]:
    """최근 N주 증감률 평균과 라벨."""
    recent = series.tail(c.momentum_weeks)
    if recent.empty:
        return float("nan"), "데이터없음"
    avg = recent.mean()
    if avg >= c.momentum_up:
        return avg, "상승"
    if avg <= c.momentum_down:
        return avg, "하락"
    return avg, "보합"


def _overall(jeonse: str, demand: str, sale_mom: str, c: SignalConfig) -> tuple[str, str]:
    """지역별 종합 시그널 + 근거 문장. 매수 트리거는 매수세우위(사다리) 기준."""
    crunch = jeonse in ("전세난", "매매전이")
    strong_demand = demand == "매수신호"          # 매수세우위 ≥20
    rising_demand = demand in ("강함", "매수신호")  # ≥15
    rising = sale_mom == "상승"

    if crunch and strong_demand and rising:
        return "STRONG_BUY", "전세난+매수세우위 20↑+매매상승 — 3박자 매수 시그널"
    if strong_demand:
        return "BUY", "매수세우위 20↑ — 적극 매수 구간"
    if crunch and (rising_demand or rising):
        return "BUY", "전세난 진행 + 매수세/가격 반응 시작"
    if jeonse == "타이트" and (rising_demand or rising):
        return "WATCH", "전세 타이트 — 전세난 전환 여부 관찰"
    if sale_mom == "하락" and demand in ("매우약함", "약함"):
        return "SELL_RISK", "매수세 위축 + 가격 하락 — 매도/관망 구간"
    return "NEUTRAL", "뚜렷한 시그널 없음"


def evaluate(kb: KBWeekly, config: SignalConfig | None = None) -> pd.DataFrame:
    """지역별 최신 시그널 테이블 산출."""
    c = config or SignalConfig()
    latest = kb.latest()

    def _get(region, metric):
        return latest.at[region, metric] if metric in latest and region in latest.index else float("nan")

    rows = []
    for region in latest.index:
        js = _get(region, "jeonse_supply")
        bd = _get(region, "buyer_demand")        # 매수세우위(raw) — 사다리/시그널 트리거
        bs = _get(region, "buyer_superiority")   # 매수우위지수 — 참고용(별개)
        sale_avg, sale_mom = _momentum(kb.series(region, "sale_change"), c)
        jeonse_avg, _ = _momentum(kb.series(region, "jeonse_change"), c)

        jeonse_state = _jeonse_state(js, c) if pd.notna(js) else "—"
        demand_state = _demand_state(bd, c) if pd.notna(bd) else "—"
        signal, reason = _overall(jeonse_state, demand_state, sale_mom, c)

        rows.append(
            {
                "region": region,
                "signal": signal,
                "전세수급": round(js, 1) if pd.notna(js) else None,
                "전세상태": jeonse_state,
                "매수세우위": round(bd, 1) if pd.notna(bd) else None,
                "매수상태": demand_state,
                "매수우위지수": round(bs, 1) if pd.notna(bs) else None,
                f"매매{c.momentum_weeks}주": round(sale_avg, 3) if pd.notna(sale_avg) else None,
                "매매모멘텀": sale_mom,
                f"전세{c.momentum_weeks}주": round(jeonse_avg, 3) if pd.notna(jeonse_avg) else None,
                "근거": reason,
            }
        )

    order = {"STRONG_BUY": 0, "BUY": 1, "WATCH": 2, "NEUTRAL": 3, "SELL_RISK": 4}
    df = pd.DataFrame(rows)
    return df.sort_values(by="signal", key=lambda s: s.map(order)).reset_index(drop=True)
