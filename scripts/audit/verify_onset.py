"""사용자의 실제 결정 시점 기준 백테스트.

성적표(engine.backtest_summary)는 구간 **종료 후** 12주를 잰다.
그런데 사용자는 화면에 STRONG_BUY 가 떠 있는 동안 산다 — 즉 구간 **시작(온셋)** 시점이 결정 지점이다.
같은 데이터로 온셋 기준 수익률을 재고, 같은 날짜의 시장 평균(base rate)과 대조한다.
"""
import statistics as st

import pandas as pd

from realty_signal.services import market_data as md
from realty_signal.signals.engine import price_index_from, signal_history

kb = md.kb()
cfg = md.signal_config()

idx_of = {}
for region in kb.latest().index:
    s = kb.series(region, "sale_change")
    if not s.empty:
        idx_of[region] = price_index_from(s)

HORIZONS = [12, 26, 52]


def fwd(idx, at, weeks):
    """at 시점 지수 대비 weeks 주 뒤 변화율(%). 데이터 부족하면 None."""
    i0 = idx.asof(at)
    if pd.isna(i0) or not i0:
        return None
    after = idx[idx.index > at]
    if len(after) < weeks:
        return None
    return (after.iloc[weeks - 1] / i0 - 1) * 100


# ── 1. 온셋 수집 ──────────────────────────────────────────────────────────
onsets = {"STRONG_BUY": [], "BUY": [], "SELL": []}
for region in kb.latest().index:
    try:
        for iv in signal_history(kb, region, cfg):
            t = iv.get("signal")
            if t in onsets:
                onsets[t].append((region, pd.Timestamp(iv["start"]),
                                  pd.Timestamp(iv["end"])))
    except Exception:
        continue

# ── 2. 같은 날짜의 시장 전체 평균(시간 매칭 base rate) ────────────────────
def market_at(date, weeks):
    vals = [fwd(idx, date, weeks) for idx in idx_of.values()]
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals)) if vals else None


print("===== 온셋(시그널 켜지는 주) 기준 · 성적표(구간 종료) 기준 비교 =====")
print(f"{'시그널':11s} {'기준점':10s} {'H':>4s} {'N':>6s} {'상승률':>8s} {'평균수익':>9s} "
      f"{'같은날 시장평균':>14s} {'초과수익':>9s}")

rows = []
for t, items in onsets.items():
    if not items:
        continue
    for anchor_name, pick in (("온셋", 0), ("구간종료", 1)):
        for h in HORIZONS:
            rets, mkts = [], []
            for region, s, e in items:
                idx = idx_of.get(region)
                if idx is None:
                    continue
                at = s if pick == 0 else e
                r = fwd(idx, at, h)
                if r is None:
                    continue
                m = market_at(at, h)
                if m is None:
                    continue
                rets.append(r)
                mkts.append(m)
            if not rets:
                continue
            upr = sum(1 for r in rets if r > 0) / len(rets) * 100
            excess = st.mean([r - m for r, m in zip(rets, mkts)])
            print(f"{t:11s} {anchor_name:10s} {h:3d}w {len(rets):6,} {upr:7.1f}% "
                  f"{st.mean(rets):+8.2f}% {st.mean(mkts):+13.2f}% {excess:+8.2f}%p")
            rows.append((t, anchor_name, h, len(rets), upr, st.mean(rets),
                         st.mean(mkts), excess))
    print()

# ── 3. 거래비용 대조 ──────────────────────────────────────────────────────
print("===== 거래비용 대조 (실거주 1주택 · 9억 기준 개략) =====")
print("취득세 1.1~3.3% + 중개 0.4~0.7% + 법무/등기 ~0.2%  →  왕복 최소 약 2~4%")
print("→ 위 표의 '초과수익'이 이 밴드를 넘지 못하면, 타이밍 판단의 실익은 비용 안에서 소멸한다.")

# ── 4. 광역 상속으로 인한 표본 독립성 점검 ────────────────────────────────
print("\n===== 표본 독립성 점검 =====")
raw = sum(1 for r in kb.latest().index if not kb.series(r, "jeonse_supply").empty)
print(f"전체 지역 {len(list(kb.latest().index))}개 중 전세수급 원본 보유 {raw}개 "
      f"→ 나머지 {len(list(kb.latest().index)) - raw}개는 상위 광역 지표를 상속.")
print("상속 지역들은 같은 매수우위·전세수급 값을 공유하므로 시그널 구간이 동조한다.")
print("→ 성적표의 평가수(393/1169/1780)는 독립 관측 수가 아니다. 신뢰구간은 실제보다 좁게 나온다.")
