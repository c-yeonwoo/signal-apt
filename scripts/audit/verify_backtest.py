"""시그널 성적표(적중률 86/82/77%)의 base rate · 리프트 · 순열검정.

절대 원칙 8: 비율 지표는 base rate 없이 발견이 아니다.
성적표 정의 그대로 재현한 뒤, (a) 무조건부 base rate, (b) 시간 매칭 순열검정을 돌린다.
"""
import random
import statistics as st

import pandas as pd

from realty_signal.services import market_data as md
from realty_signal.signals.engine import price_index_from, signal_history

kb = md.kb()
cfg = md.signal_config()

# ── 1. 지역별 누적 가격지수 (성적표와 동일 산식) ────────────────────────────
idx_of = {}
for region in kb.latest().index:
    s = kb.series(region, "sale_change")
    if not s.empty:
        idx_of[region] = price_index_from(s)

print(f"지역 수: {len(idx_of)}")

# ── 2. 무조건부 base rate: 모든 (지역, 주) 의 이후 12주 변화 ────────────────
#     성적표 hit 판정과 동일하게 round(...,1) 후 > 0 비교
allf, up, zero = [], 0, 0
for region, idx in idx_of.items():
    v = idx.values
    for i in range(len(v) - 12):
        if not v[i]:
            continue
        pct = round((v[i + 12] / v[i] - 1) * 100, 1)
        allf.append(pct)
        if pct > 0:
            up += 1
        elif pct == 0:
            zero += 1

n = len(allf)
p_up = up / n * 100
print("\n===== 무조건부 base rate (모든 지역·모든 주) =====")
print(f"표본 {n:,}  ·  상승 {p_up:.1f}%  ·  정확히 0.0 {zero/n*100:.1f}%  ·  하락 {(n-up-zero)/n*100:.1f}%")
print(f"평균 12주 변화 {st.mean(allf):+.2f}%  ·  중앙값 {st.median(allf):+.2f}%")

# ── 3. 실제 시그널 구간 수집 (성적표와 동일 경로) ───────────────────────────
ivs_by_type = {"STRONG_BUY": [], "BUY": [], "SELL": []}
for region in kb.latest().index:
    try:
        for iv in signal_history(kb, region, cfg):
            t = iv.get("signal")
            if t in ivs_by_type and iv.get("after12w_pct") is not None:
                ivs_by_type[t].append({"region": region, "end": iv["end"],
                                       "after": iv["after12w_pct"]})
    except Exception:
        continue

print("\n===== 실제 성적표 재현 =====")
real = {}
for t, ivs in ivs_by_type.items():
    if not ivs:
        continue
    # SELL 의 적중 = not up = (after <= 0) — 성적표 engine.py:334 와 동일
    hit = sum(1 for x in ivs if ((x["after"] > 0) if t != "SELL" else (x["after"] <= 0)))
    real[t] = hit / len(ivs) * 100
    print(f"{t:11s} 평가 {len(ivs):5,}  적중률 {real[t]:5.1f}%  "
          f"이후12주평균 {st.mean([x['after'] for x in ivs]):+.2f}%")

# ── 4. 시간 매칭 순열검정 ────────────────────────────────────────────────
#     "같은 날짜, 무작위 다른 지역" 으로 바꿔도 같은 적중률이 나오는가?
#     나온다면 그 적중률은 시그널이 아니라 '그 시기 시장'이 만든 것이다.
def forward_at(region, end_date):
    idx = idx_of.get(region)
    if idx is None:
        return None
    e = pd.Timestamp(end_date)
    i1 = idx.asof(e)
    if pd.isna(i1) or not i1:
        return None
    after = idx[idx.index > e]
    if not len(after):
        return None
    a = after.iloc[min(11, len(after) - 1)]
    if pd.isna(a):
        return None
    return round((a / i1 - 1) * 100, 1)


regions = list(idx_of.keys())
rng = random.Random(20260817)
TRIALS = 200

print("\n===== 순열검정: 같은 날짜 · 무작위 지역 (200회) =====")
print(f"{'시그널':11s} {'실제':>7s} {'순열평균':>9s} {'순열 5~95%':>16s} {'리프트':>8s}  판정")
perm_out = {}
for t, ivs in ivs_by_type.items():
    if not ivs:
        continue
    rates = []
    for _ in range(TRIALS):
        h = c = 0
        for x in ivs:
            r = regions[rng.randrange(len(regions))]
            f = forward_at(r, x["end"])
            if f is None:
                continue
            c += 1
            if (f > 0) if t != "SELL" else (f <= 0):
                h += 1
        if c:
            rates.append(h / c * 100)
    rates.sort()
    lo, hi = rates[int(.05 * len(rates))], rates[int(.95 * len(rates))]
    mean = st.mean(rates)
    lift = real[t] - mean
    verdict = "시그널 기여 있음" if real[t] > hi else ("무작위와 구분 안 됨" if real[t] >= lo else "무작위보다 나쁨")
    perm_out[t] = (real[t], mean, lo, hi, lift, verdict)
    print(f"{t:11s} {real[t]:6.1f}% {mean:8.1f}% {lo:7.1f}~{hi:5.1f}% {lift:+7.1f}%p  {verdict}")

# ── 5. 참고: 시그널 구간의 시기 분포 ──────────────────────────────────────
print("\n===== 시그널 구간 종료 시점의 연도 분포 =====")
for t, ivs in ivs_by_type.items():
    if not ivs:
        continue
    yrs = {}
    for x in ivs:
        y = x["end"][:4]
        yrs[y] = yrs.get(y, 0) + 1
    top = sorted(yrs.items(), key=lambda kv: -kv[1])[:6]
    print(f"{t:11s} " + "  ".join(f"{y}:{c}" for y, c in top))
