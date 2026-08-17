"""채점 규칙(_classify, 백테스트) vs 노출 규칙(evaluate, 화면) 괴리 측정.

레포 루트에서 `.venv/bin/python <이 파일>` 로 실행.
"""
from __future__ import annotations

import sys
from collections import Counter

import pandas as pd

from realty_signal.services import market_data as md
from realty_signal.signals import engine as E

c = md.signal_config()
kb = md.kb()
print(f"기준일 {kb.last_date.date()} · 지역수 {len(kb.latest().index)}")
print(f"config: buyeridx_strong={c.buyeridx_strong} mid={c.buyeridx_mid} "
      f"jeonse_crunch={c.jeonse_crunch} demand_buy={c.demand_buy} "
      f"supply_glut={c.supply_glut} momentum_weeks={c.momentum_weeks}")

# ---------------------------------------------------------------- A. 최신주
screen = md.signals_df()                    # evaluate(): 화면·브리핑·챗봇·추천 정본
screen_map = dict(zip(screen["region"], screen["signal"]))
inherit_map = dict(zip(screen["region"], screen["수급출처"]))
supply_map = dict(zip(screen["region"], screen["공급압력"]))

latest = kb.latest()
have = set(latest.index)


def parent_of(region):
    if region in ("서울", "경기", "인천", "강남11개구", "강북14개구"):
        return None
    if region in E._SEOUL_GANGNAM and "강남11개구" in have:
        return "강남11개구"
    if region in E._SEOUL_GANGBUK and "강북14개구" in have:
        return "강북14개구"
    code = (kb.codes or {}).get(region, "") or ""
    return {"11": "서울", "41": "경기", "28": "인천", "46": "전남"}.get(code[:2])


def get(region, metric):
    return latest.at[region, metric] if metric in latest and region in latest.index else float("nan")


rows = []
for region in latest.index:
    js, bd, bs = get(region, "jeonse_supply"), get(region, "buyer_demand"), get(region, "buyer_superiority")
    if pd.isna(js):
        p = parent_of(region)
        if p and pd.notna(get(p, "jeonse_supply")):
            js, bd, bs = get(p, "jeonse_supply"), get(p, "buyer_demand"), get(p, "buyer_superiority")
    _, mom_lbl = E._momentum(kb.series(region, "sale_change"), c)
    jstate = E._jeonse_state(js, c) if pd.notna(js) else "—"
    # 백테스트 규칙: supply 미전달, bear-score 없음
    bt_sig, _ = E._classify(js, bs, bd, jstate, mom_lbl, c)
    bt_state = bt_sig if bt_sig in ("STRONG_BUY", "BUY") else ("SELL" if mom_lbl == "하락" else None)
    rows.append({
        "region": region, "backtest": bt_sig, "backtest_state": bt_state,
        "screen": screen_map.get(region), "상속": inherit_map.get(region),
        "공급압력": supply_map.get(region), "모멘텀": mom_lbl,
    })

df = pd.DataFrame(rows)
n = len(df)
diff = df[df["backtest"] != df["screen"]]
print("\n=== A. 최신주 117지역: 백테스트 규칙(_classify) vs 화면 규칙(evaluate) ===")
print(f"불일치 {len(diff)}/{n} = {len(diff)/n*100:.1f}%")
print(Counter(zip(diff["backtest"], diff["screen"])).most_common())
print("\n백테스트 라벨 분포:", dict(Counter(df["backtest"])))
print("화면 라벨 분포  :", dict(Counter(df["screen"])))
print("\n백테스트 채점상태(성적표 행) 분포:", dict(Counter(df["backtest_state"].fillna("미채점"))))

# 백테스트가 매수(STRONG_BUY/BUY)로 채점하는데 화면은 매수가 아닌 지역
bt_buy = df[df["backtest"].isin(["STRONG_BUY", "BUY"])]
flip = bt_buy[~bt_buy["screen"].isin(["STRONG_BUY", "BUY"])]
print(f"\n백테스트=매수인데 화면=비매수: {len(flip)}/{len(bt_buy)}")
sc_buy = df[df["screen"].isin(["STRONG_BUY", "BUY"])]
flip2 = sc_buy[~sc_buy["backtest"].isin(["STRONG_BUY", "BUY"])]
print(f"화면=매수인데 백테스트=비매수: {len(flip2)}/{len(sc_buy)}")

# 상속 비율
print(f"\n상속 지역(수급출처 있음): {df['상속'].notna().sum()}/{n}")
print(f"  그중 화면 STRONG_BUY/BUY: {sc_buy['상속'].notna().sum()}/{len(sc_buy)}")
print(f"공급압력 데이터 있는 지역: {df['공급압력'].notna().sum()}/{n}")

# ------------------------------------------------- B. 전체 이력 (지역 x 주)
print("\n=== B. 전체 이력: 성적표 채점라벨 vs _classify 등급 ===")
tot = 0
sell_rows = 0
sell_not_sellrisk = 0
sell_by_classify = Counter()
allpairs = Counter()
for region in latest.index:
    js = kb.series(region, "jeonse_supply")
    bs = kb.series(region, "buyer_superiority")
    bd = kb.series(region, "buyer_demand")
    if js.empty:
        p = parent_of(region)
        if p:
            js, bs, bd = kb.series(p, "jeonse_supply"), kb.series(p, "buyer_superiority"), kb.series(p, "buyer_demand")
    sale = kb.series(region, "sale_change")
    if sale.empty:
        continue
    dates = list(sale.index)
    vals = sale.values
    for i, d in enumerate(dates):
        jv = js.asof(d) if not js.empty else float("nan")
        bv = bs.asof(d) if not bs.empty else float("nan")
        dv = bd.asof(d) if not bd.empty else float("nan")
        w = vals[max(0, i - c.momentum_weeks + 1): i + 1]
        mom = w.mean() if len(w) else float("nan")
        mom_lbl = "상승" if mom >= c.momentum_up else ("하락" if mom <= c.momentum_down else "보합")
        jstate = E._jeonse_state(jv, c) if pd.notna(jv) else "—"
        sig, _ = E._classify(jv, bv, dv, jstate, mom_lbl, c)
        state = sig if sig in ("STRONG_BUY", "BUY") else ("SELL" if mom_lbl == "하락" else None)
        tot += 1
        allpairs[(sig, state)] += 1
        if state == "SELL":
            sell_rows += 1
            sell_by_classify[sig] += 1
            if sig != "SELL_RISK":
                sell_not_sellrisk += 1

print(f"총 (지역,주) 표본 {tot:,}")
print(f"성적표 'SELL' 로 채점되는 주  {sell_rows:,} ({sell_rows/tot*100:.1f}%)")
print(f"  그중 화면 규칙 SELL_RISK 가 아닌 주 {sell_not_sellrisk:,} "
      f"({sell_not_sellrisk/max(1,sell_rows)*100:.1f}% of SELL)")
print("  SELL 로 채점된 주의 _classify 등급 분포:", dict(sell_by_classify))
print("\n(_classify 등급, 성적표 채점라벨) 상위:", allpairs.most_common(10))
