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

# 서울 한강 이남/이북 권역 (KB 강남11개구/강북14개구 수급 상속용)
_SEOUL_GANGNAM = {"양천구", "강서구", "구로구", "금천구", "영등포구", "동작구",
                  "관악구", "서초구", "강남구", "송파구", "강동구"}
_SEOUL_GANGBUK = {"종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구",
                  "성북구", "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구"}


@dataclass
class SignalConfig:
    # 전세수급지수 밴드 경계
    jeonse_oversupply: float = 100.0
    jeonse_tight: float = 140.0
    jeonse_crunch: float = 170.0
    jeonse_spillover: float = 190.0
    # 매수우위지수(차트 기준, 우선) — KB 실측 median≈46, p75≈70, 100↑은 활황.
    buyeridx_mid: float = 50.0   # 중립 이상
    buyeridx_strong: float = 70.0  # 강세(상위권)
    # 매수세우위(raw) 사다리 — 원본 메모 "5/10/15/20, 20↑ 매수"(참고용). median≈3~5.
    demand_l1: float = 5.0   # 약함
    demand_l2: float = 10.0  # 보통
    demand_l3: float = 15.0  # 강함
    demand_buy: float = 20.0  # 매수신호 ("빨리 사야한다")
    # 모멘텀 산정 주(week) 수, 증감률 임계(%)
    momentum_weeks: int = 4
    momentum_up: float = 0.05
    momentum_down: float = -0.05
    # 입주물량 공급압력(향후/과거) 임계
    supply_glut: float = 1.3   # 공급과잉 → 매도/하락 압력
    supply_dry: float = 0.7    # 공급부족 → 매수 우호


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


def _classify(
    js: float,
    bs: float,
    bd: float,
    jeonse_state: str,
    sale_mom: str,
    c: SignalConfig,
    supply: float = float("nan"),
) -> tuple[str, list[str]]:
    """OR 조건 종합 시그널 + 조건별 근거 리스트.

    차트 기준(전세수급·매수우위지수·매매모멘텀)을 우선 판정하고,
    메모 기준(매수세우위 사다리)은 [참고] 근거로만 덧붙인다.
    입주물량 공급압력(supply)은 매도/하락 압력 보정에 사용한다.
    """
    reasons: list[str] = []

    # --- 차트 기준 (우선) ---
    crunch = jeonse_state in ("전세난", "매매전이")
    if jeonse_state == "매매전이":
        reasons.append(f"전세난→매매전이(전세수급 {js:.0f})")
    elif jeonse_state == "전세난":
        reasons.append(f"전세난(전세수급 {js:.0f})")
    elif jeonse_state == "타이트":
        reasons.append(f"전세 타이트(전세수급 {js:.0f})")

    idx_strong = pd.notna(bs) and bs >= c.buyeridx_strong
    idx_mid = pd.notna(bs) and bs >= c.buyeridx_mid
    if idx_strong:
        reasons.append(f"매수우위지수 강세({bs:.0f})")
    elif idx_mid:
        reasons.append(f"매수우위지수 중립↑({bs:.0f})")

    rising = sale_mom == "상승"
    if rising:
        reasons.append("매매 상승세")
    elif sale_mom == "하락":
        reasons.append("매매 하락세")

    # --- 메모 기준 (참고) ---
    if pd.notna(bd) and bd >= c.demand_buy:
        reasons.append(f"[참고] 매수세우위 20↑({bd:.0f})")
    elif pd.notna(bd) and bd >= c.demand_l3:
        reasons.append(f"[참고] 매수세우위 강함({bd:.0f})")

    # 차트 기준 강세 조건 개수로 등급 결정 (OR)
    chart_bull = sum([crunch, idx_strong, rising])
    if chart_bull >= 3:
        signal = "STRONG_BUY"
    elif chart_bull == 2:
        signal = "BUY"
    elif chart_bull == 1 or jeonse_state == "타이트":
        signal = "WATCH"
    elif sale_mom == "하락" and not idx_mid:
        signal = "SELL_RISK"
    else:
        signal = "NEUTRAL"

    # --- 입주물량 공급압력 보정 (매도/끝물) ---
    if pd.notna(supply):
        if supply >= c.supply_glut:
            reasons.append(f"공급과잉(향후 입주 {supply:g}배)")
            if sale_mom == "하락":
                signal = "SELL_RISK"  # 입주폭탄 + 가격하락 = 매도 구간
            elif signal in ("STRONG_BUY", "BUY"):
                reasons.append("⚠️입주부담 주의")
        elif supply <= c.supply_dry:
            reasons.append(f"공급부족(향후 입주 {supply:g}배)")

    if not reasons:
        reasons.append("뚜렷한 시그널 없음")
    return signal, reasons


def interpret(signal: str, jeonse_state: str, bs: float, demand_state: str,
              sale_mom: str, supply: float, c: SignalConfig) -> str:
    """지수들을 종합한 1~2문장 해설 — 사회/통계적 의미를 풀어 설명."""
    idx_strong = pd.notna(bs) and bs >= c.buyeridx_strong
    idx_mid = pd.notna(bs) and bs >= c.buyeridx_mid
    parts: list[str] = []

    # 1) 전세 수급 국면 (전세→매매 전이 메커니즘)
    if jeonse_state == "매매전이":
        parts.append("전세난이 심화돼 전세 수요가 매매로 강하게 전이되는 국면으로, 매매가 상승 압력이 큽니다")
    elif jeonse_state == "전세난":
        parts.append("전세 매물 부족(전세난)으로 전세가가 오르며 매매 전환 수요가 유입되는 구간입니다")
    elif jeonse_state == "타이트":
        parts.append("전세 수급이 타이트해지는 관찰 구간으로, 전세난으로 번지면 매매 상승으로 이어질 수 있습니다")
    elif jeonse_state == "공급우위":
        parts.append("전세 공급이 충분해 전세가 안정세이며 매매 상방 압력은 약합니다")

    # 2) 매수심리 + 가격 모멘텀
    if idx_strong and sale_mom == "상승":
        parts.append("매수우위지수가 높고 가격도 오르고 있어 매수세가 몰리며 단기 추가 상승 가능성이 높습니다")
    elif idx_strong:
        parts.append("매수세가 매도세를 앞서 매수자 우위 시장으로 전환되는 신호입니다")
    elif sale_mom == "상승" and not parts:
        parts.append("가격이 상승 중이나 수급·심리 지표가 약해 추세 지속 여부는 관찰이 필요합니다")
    elif sale_mom == "하락":
        parts.append("매수세가 위축되고 가격이 하락 전환되는 약세 신호입니다")

    # 3) 입주물량(공급) 보정
    if pd.notna(supply) and supply >= c.supply_glut:
        parts.append(f"다만 향후 입주물량이 평년의 {supply:g}배로 많아 중기 공급부담이 가격 상단을 누를 수 있습니다")
    elif pd.notna(supply) and supply <= c.supply_dry and signal in ("STRONG_BUY", "BUY"):
        parts.append("입주물량도 적어 공급 측 하방 압력은 제한적입니다")

    if not parts:
        parts.append("지표상 뚜렷한 방향성이 없는 관망 구간입니다")
    return ". ".join(parts[:2]) + "."


def _hist_reason(kind: str, jv: float, bv: float, dv: float, mom_lbl: str, c: SignalConfig) -> str:
    """과거 구간 시작 시점의 지표로 그 시그널의 근거 문장 생성 (구간마다 다름)."""
    p = []
    if kind == "SELL":
        p.append(f"매매 {mom_lbl} 모멘텀" if mom_lbl == "하락" else "매매 약세")
        if pd.notna(jv) and jv < c.jeonse_tight:
            p.append(f"전세수급 약함({jv:.0f})")
        if pd.notna(bv) and bv < c.buyeridx_strong * 0.5:
            p.append(f"매수심리 위축({bv:.0f})")
    else:
        if pd.notna(jv) and jv >= c.jeonse_crunch:
            p.append(f"전세난(전세수급 {jv:.0f})")
        elif pd.notna(jv) and jv >= c.jeonse_tight:
            p.append(f"전세 타이트({jv:.0f})")
        if pd.notna(bv) and bv >= c.buyeridx_strong:
            p.append(f"매수우위 강세({bv:.0f})")
        elif pd.notna(dv) and dv >= c.demand_buy:
            p.append(f"매수세 우위({dv:.0f})")
        if mom_lbl == "상승":
            p.append("상승 모멘텀")
    return " · ".join(p) or "복합 신호"


def signal_history(kb: KBWeekly, region: str, config: SignalConfig | None = None) -> list[dict]:
    """지역의 과거 주간 시그널을 역산해 STRONG_BUY/BUY 연속 구간 반환.

    각 주: 전세수급·매수우위지수(시군구는 상위 광역 상속) + 직전 N주 매매모멘텀 → _classify.
    """
    c = config or SignalConfig()
    js = kb.series(region, "jeonse_supply")
    bs = kb.series(region, "buyer_superiority")
    bd = kb.series(region, "buyer_demand")
    if js.empty:  # 시군구 → 상위 광역 상속
        code = (kb.codes or {}).get(region, "") or ""
        parent = None
        if region in _SEOUL_GANGNAM:
            parent = "강남11개구"
        elif region in _SEOUL_GANGBUK:
            parent = "강북14개구"
        else:
            parent = {"11": "서울", "41": "경기", "28": "인천", "46": "전남"}.get(code[:2])
        if parent:
            js, bs, bd = kb.series(parent, "jeonse_supply"), kb.series(parent, "buyer_superiority"), kb.series(parent, "buyer_demand")
    sale = kb.series(region, "sale_change")
    if sale.empty:
        return []
    idx = price_index_from(sale)  # 누적 매매가격지수(검증용)

    dates = list(sale.index)
    intervals, cur = [], None
    for i, d in enumerate(dates):
        jv = js.asof(d) if not js.empty else float("nan")
        bv = bs.asof(d) if not bs.empty else float("nan")
        dv = bd.asof(d) if not bd.empty else float("nan")
        mom = sale.iloc[max(0, i - c.momentum_weeks + 1): i + 1].mean()
        mom_lbl = "상승" if mom >= c.momentum_up else ("하락" if mom <= c.momentum_down else "보합")
        sig, _ = _classify(jv, bv, dv, _jeonse_state(jv, c) if pd.notna(jv) else "—", mom_lbl, c)
        # 상태: 매수(hot) / 매도(매매 하락 지속) / 중립
        if sig in ("STRONG_BUY", "BUY"):
            state = sig
        elif mom_lbl == "하락":
            state = "SELL"
        else:
            state = None
        kind = "SELL" if state == "SELL" else ("BUY" if state else None)  # 같은 종류끼리 연결
        if state and cur is None:
            cur = {"start": str(d.date()), "signal": sig if state != "SELL" else "SELL",
                   "_kind": kind, "근거": _hist_reason(kind, jv, bv, dv, mom_lbl, c)}
        elif state and cur["_kind"] == kind:
            if state == "STRONG_BUY":
                cur["signal"] = "STRONG_BUY"
        elif cur is not None:
            cur["end"] = str(dates[i - 1].date())
            intervals.append(cur)
            cur = {"start": str(d.date()), "signal": sig if state != "SELL" else "SELL",
                   "_kind": kind, "근거": _hist_reason(kind, jv, bv, dv, mom_lbl, c)} if state else None
    if cur is not None:
        cur["end"] = str(dates[-1].date())
        intervals.append(cur)
    for iv in intervals:
        iv.pop("_kind", None)

    # 각 구간: 기간 중 상승률 + 이후 12주 변화율 (실제 결과 검증)
    for iv in intervals:
        s, e = pd.Timestamp(iv["start"]), pd.Timestamp(iv["end"])
        i0, i1 = idx.asof(s), idx.asof(e)
        if pd.notna(i0) and pd.notna(i1) and i0:
            iv["during_pct"] = round((i1 / i0 - 1) * 100, 1)
        after = idx[idx.index > e]
        a = after.iloc[min(11, len(after) - 1)] if len(after) else None
        if a is not None and pd.notna(i1) and i1:
            iv["after12w_pct"] = round((a / i1 - 1) * 100, 1)
    return intervals


def backtest_summary(kb: KBWeekly, config: SignalConfig | None = None) -> dict:
    """전 지역 과거 시그널 구간을 모아 타입별 적중률·평균 수익률 집계.

    적중 정의 — 매수(STRONG_BUY/BUY): 시그널 이후 12주 가격 상승. 매도(SELL): 이후 12주 하락.
    오직 보유 데이터(KB 누적 가격지수)만으로 '이 신호가 과거에 맞았나'를 수치화.
    """
    c = config or SignalConfig()
    agg: dict = {}
    for region in kb.latest().index:
        try:
            ivs = signal_history(kb, region, c)
        except Exception:
            continue
        for iv in ivs:
            t = iv.get("signal")
            if t not in ("STRONG_BUY", "BUY", "SELL"):
                continue
            a = agg.setdefault(t, {"n": 0, "during": [], "after": [], "hit": 0, "neval": 0})
            a["n"] += 1
            if iv.get("during_pct") is not None:
                a["during"].append(iv["during_pct"])
            if iv.get("after12w_pct") is not None:
                a["after"].append(iv["after12w_pct"])
                a["neval"] += 1
                up = iv["after12w_pct"] > 0
                if (t in ("STRONG_BUY", "BUY") and up) or (t == "SELL" and not up):
                    a["hit"] += 1

    def _avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    by = []
    for t in ("STRONG_BUY", "BUY", "SELL"):
        a = agg.get(t)
        if not a:
            continue
        by.append({
            "signal": t, "구간수": a["n"], "평가수": a["neval"],
            "적중률": round(a["hit"] / a["neval"] * 100) if a["neval"] else None,
            "기간중평균": _avg(a["during"]), "이후12주평균": _avg(a["after"]),
        })
    return {"기준일": str(kb.last_date.date()), "by_signal": by,
            "설명": "과거 각 지역 시그널 구간의 실제 가격 변화. 매수=이후 12주 상승, 매도=이후 12주 하락이면 적중."}


def price_index_from(sale: "pd.Series") -> "pd.Series":
    """주간 매매증감률(%) → 누적 매매가격지수(시작=100)."""
    s = sale.sort_index().dropna()
    return (1 + s / 100).cumprod() * 100 if not s.empty else s


def macro_trend(macro: dict) -> dict:
    """대출금리·구매력 최근 추세(상승/하락/보합) 요약."""
    def dir_of(arr):
        v = [x for x in (arr or []) if x is not None]
        if len(v) < 7:
            return None, (v[-1] if v else None)
        diff = v[-1] - v[-7]
        return ("상승" if diff > 0.05 else "하락" if diff < -0.05 else "보합"), v[-1]
    rd, rv = dir_of(macro.get("대출금리"))
    pd_, pv = dir_of(macro.get("구매력"))
    return {"rate_dir": rd, "rate": rv, "power_dir": pd_, "power": pv}


def evaluate(
    kb: KBWeekly, config: SignalConfig | None = None, supply: pd.DataFrame | None = None,
    macro: dict | None = None, volumes: dict | None = None, regime: dict | None = None,
) -> pd.DataFrame:
    """지역별 최신 시그널 테이블 산출. supply: 입주물량 공급압력 테이블(선택)."""
    c = config or SignalConfig()
    latest = kb.latest()
    sp_map = (
        dict(zip(supply["region"], supply["supply_pressure"]))
        if supply is not None and not supply.empty
        else {}
    )

    def _get(region, metric):
        return latest.at[region, metric] if metric in latest and region in latest.index else float("nan")

    # 거시 환경 한 줄 (금리·구매력)
    mt = macro_trend(macro or {})
    macro_clause = ""
    if mt["rate"] is not None:
        if mt["rate_dir"] == "하락":
            macro_clause = f"대출금리 하락기(현 {mt['rate']}%)로 매수 여력이 개선되는 거시환경"
        elif mt["rate_dir"] == "상승":
            macro_clause = f"대출금리 상승기(현 {mt['rate']}%)로 매수 부담이 커지는 거시환경"
        else:
            macro_clause = f"대출금리 보합(현 {mt['rate']}%) 환경"
        if mt["power_dir"] == "상승":
            macro_clause += ", 주택구매력도 개선 중"
        elif mt["power_dir"] == "하락":
            macro_clause += ", 주택구매력은 약화 중"

    have = set(latest.index)

    def _parent(region):
        """수급·심리 미조사 시군구 → 상위 권역.
        서울은 KB 강남11개구/강북14개구 권역으로 구분 상속, 그 외 광역(경기/인천)."""
        if region in ("서울", "경기", "인천", "강남11개구", "강북14개구"):
            return None
        if region in _SEOUL_GANGNAM and "강남11개구" in have:
            return "강남11개구"
        if region in _SEOUL_GANGBUK and "강북14개구" in have:
            return "강북14개구"
        code = (kb.codes or {}).get(region, "") or ""
        return {"11": "서울", "41": "경기", "28": "인천", "46": "전남"}.get(code[:2])

    rows = []
    for region in latest.index:
        js = _get(region, "jeonse_supply")
        bd = _get(region, "buyer_demand")        # 매수세우위(raw)
        bs = _get(region, "buyer_superiority")   # 매수우위지수
        sale_avg, sale_mom = _momentum(kb.series(region, "sale_change"), c)
        jeonse_avg, _ = _momentum(kb.series(region, "jeonse_change"), c)

        # 시군구는 수급·심리 미조사 → 상위 광역값 상속 (자체 매매모멘텀과 결합)
        inherited_from = None
        if pd.isna(js):
            p = _parent(region)
            if p and pd.notna(_get(p, "jeonse_supply")):
                js, bd, bs = _get(p, "jeonse_supply"), _get(p, "buyer_demand"), _get(p, "buyer_superiority")
                inherited_from = p

        jeonse_state = _jeonse_state(js, c) if pd.notna(js) else "—"
        demand_state = _demand_state(bd, c) if pd.notna(bd) else "—"
        sp = sp_map.get(region, float("nan"))
        signal, reasons = _classify(js, bs, bd, jeonse_state, sale_mom, c, sp)
        해설 = interpret(signal, jeonse_state, bs, demand_state, sale_mom, sp, c)
        if inherited_from:
            reasons.append(f"※수급·심리는 {inherited_from} 광역 기준")
            해설 = f"({inherited_from} 광역 수급 + {region} 매매흐름) " + 해설
        vr = (volumes or {}).get(region, {}).get("거래량비")
        if vr is not None:
            if vr >= 1.2:
                reasons.append(f"거래량 급증({vr}배)")
                해설 += f" 최근 거래량이 평소의 {vr}배로 매수세가 유입되고 있습니다."
            elif vr <= 0.8:
                reasons.append(f"거래량 위축({vr}배)")

        # 급지역전(권역 끝물) — 수도권
        rg = (regime or {}).get("regions", {}).get(region)
        급지 = rg["급지"] if rg else None
        if rg and rg.get("막차"):
            reasons.append("막차경고(하급지 급등)")
            해설 += " 하급지인데 최근 급등해 유동성 끝물의 막차 위험이 있습니다."
        elif rg and (regime or {}).get("endgame"):
            reasons.append("권역 끝물(급지역전)")

        # 매도(끝물) 강화 — 공급과잉 + 유동성감소(금리상승·거래량위축·급지역전·매매하락)
        bear = 0.0
        if sale_mom == "하락":
            bear += 1
        if pd.notna(sp) and sp >= c.supply_glut:
            bear += 1
        if vr is not None and vr <= 0.8:
            bear += 1
        if rg and rg.get("막차"):
            bear += 1
        if (regime or {}).get("endgame"):
            bear += 0.5
        if mt.get("rate_dir") == "상승":
            bear += 0.5
        endgame_glut = (regime or {}).get("endgame") and pd.notna(sp) and sp >= c.supply_glut
        if signal not in ("STRONG_BUY", "BUY") and bear >= 2 and (sale_mom == "하락" or endgame_glut):
            signal = "SELL_RISK"
            해설 = "공급과잉·유동성 감소가 겹치는 매도/관망 구간입니다. " + 해설

        if macro_clause:
            해설 += f" {macro_clause}입니다." if not 해설.rstrip().endswith("입니다.") else f" ({macro_clause})"

        rows.append(
            {
                "region": region,
                "signal": signal,
                "해설": 해설,
                "전세수급": round(js, 1) if pd.notna(js) else None,
                "전세상태": jeonse_state,
                "매수세우위": round(bd, 1) if pd.notna(bd) else None,
                "매수상태": demand_state,
                "매수우위지수": round(bs, 1) if pd.notna(bs) else None,
                f"매매{c.momentum_weeks}주": round(sale_avg, 3) if pd.notna(sale_avg) else None,
                "매매모멘텀": sale_mom,
                f"전세{c.momentum_weeks}주": round(jeonse_avg, 3) if pd.notna(jeonse_avg) else None,
                "공급압력": round(sp, 2) if pd.notna(sp) else None,
                "거래량비": vr,
                "급지": 급지,
                "수급출처": inherited_from,   # 전세수급·매수우위가 상속된 상위 권역(있으면 구별 공통값)
                "근거": " · ".join(reasons),
            }
        )

    order = {"STRONG_BUY": 0, "BUY": 1, "WATCH": 2, "NEUTRAL": 3, "SELL_RISK": 4}
    df = pd.DataFrame(rows)
    return df.sort_values(by="signal", key=lambda s: s.map(order)).reset_index(drop=True)
