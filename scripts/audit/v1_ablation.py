"""V-1 — 시그널의 세 재료가 각각 무엇을 더하는가.

진단에서 두 수치가 나란히 놓였다:
  · 모멘텀 단독 필터  12주 상승률 94.5% (N=33,114)
  · 성적표 STRONG_BUY 적중률 86.3%     (N=393)
그런데 **앵커가 다르다** — 앞은 조건을 만족하는 매 주차, 뒤는 시그널 구간이 끝난 시점이다.
직접 비교할 수 없는 두 숫자였다. 여기서 같은 앵커·같은 기준선으로 다시 잰다.

앵커: 조건을 만족하는 **모든 (지역, 주)**. 화면에 등급이 떠 있는 동안이 사용자의 결정 지점이므로
      구간 종료보다 이쪽이 실제 사용 상황에 가깝다.
기준선: **같은 날짜의 전체 지역** 상승률·평균수익. 절대 원칙 8 — 비율은 base rate 없이 무의미하다.
판정: 원시 상승률이 아니라 **초과분(시그널 − 같은 날짜 시장)** 으로 본다.

실행: PYTHONPATH=src .venv/bin/python scripts/audit/v1_ablation.py
"""

from __future__ import annotations

import pandas as pd

from realty_signal.services import market_data as md
from realty_signal.signals.engine import _SEOUL_GANGBUK, _SEOUL_GANGNAM, price_index_from

kb = md.kb()
c = md.signal_config()
HORIZONS = [12, 52]


def _inherited(region: str, metric: str) -> pd.Series:
    """시군구는 상위 광역 지표를 상속한다 (`signal_history` 와 동일 규칙)."""
    s = kb.series(region, metric)
    if not s.empty:
        return s
    code = (kb.codes or {}).get(region, "") or ""
    if region in _SEOUL_GANGNAM:
        parent = "강남11개구"
    elif region in _SEOUL_GANGBUK:
        parent = "강북14개구"
    else:
        parent = {"11": "서울", "41": "경기", "28": "인천", "46": "전남"}.get(code[:2])
    return kb.series(parent, metric) if parent else s


# ── 지역별 조건·수익 정렬 ────────────────────────────────────────────────
cond: dict[str, pd.DataFrame] = {}
fwd: dict[int, dict[str, pd.Series]] = {h: {} for h in HORIZONS}

for region in kb.latest().index:
    sale = kb.series(region, "sale_change")
    if sale.empty:
        continue
    idx = price_index_from(sale)
    js = _inherited(region, "jeonse_supply").reindex(sale.index, method="ffill")
    bs = _inherited(region, "buyer_superiority").reindex(sale.index, method="ffill")
    mom = sale.rolling(c.momentum_weeks).mean()

    cond[region] = pd.DataFrame({
        "crunch": js >= c.jeonse_crunch,          # 전세난·매매전이
        "idx_strong": bs >= c.buyeridx_strong,    # 매수우위지수 강세
        "rising": mom >= c.momentum_up,           # 매매 4주 모멘텀 상승
    }).fillna(False)

    for h in HORIZONS:
        fwd[h][region] = (idx.shift(-h) / idx - 1) * 100

FWD = {h: pd.DataFrame(fwd[h]) for h in HORIZONS}
CR = pd.DataFrame({r: cond[r]["crunch"] for r in cond}).fillna(False)
IX = pd.DataFrame({r: cond[r]["idx_strong"] for r in cond}).fillna(False)
RS = pd.DataFrame({r: cond[r]["rising"] for r in cond}).fillna(False)
VOTES = CR.astype(int) + IX.astype(int) + RS.astype(int)

print(f"지역 {CR.shape[1]}개 · 주 {CR.shape[0]}개 · 셀 {CR.size:,}")

# ── 같은 날짜 시장 기준선 ────────────────────────────────────────────────
MKT_UP, MKT_RET = {}, {}
for h in HORIZONS:
    f = FWD[h]
    MKT_UP[h] = (f > 0).sum(axis=1) / f.notna().sum(axis=1)   # 그 날 전체 지역 상승 비율
    MKT_RET[h] = f.mean(axis=1)

VARIANTS = {
    "① 모멘텀만":            RS,
    "② 매수우위지수만":       IX,
    "③ 전세난만":            CR,
    "④ 2조건↑ (= BUY+)":     VOTES >= 2,
    "⑤ 3조건 (= STRONG_BUY)": VOTES == 3,
    "   ⑤ − 전세난":         IX & RS,
    "   ⑤ − 매수우위":       CR & RS,
    "   ⑤ − 모멘텀":         CR & IX,
}


def measure(mask: pd.DataFrame, h: int) -> dict | None:
    f = FWD[h]
    m = mask.reindex(index=f.index, columns=f.columns).fillna(False) & f.notna()
    n = int(m.values.sum())
    if n < 30:
        return None
    vals = f.where(m)
    hit = float((vals > 0).values.sum()) / n * 100
    ret = float(vals.stack().mean())
    per_row = m.sum(axis=1)                       # 날짜별 표본 수 → 기준선 가중평균
    w = per_row / per_row.sum()
    base_hit = float((MKT_UP[h] * w).sum()) * 100
    base_ret = float((MKT_RET[h] * w).sum())
    return {"n": n, "hit": hit, "base_hit": base_hit, "lift": hit - base_hit,
            "ret": ret, "base_ret": base_ret, "excess": ret - base_ret}


for h in HORIZONS:
    print(f"\n{'='*98}\n이후 {h}주 · 앵커 = 조건을 만족하는 모든 (지역, 주)\n{'='*98}")
    print(f"{'변형':24s} {'N':>9s} {'상승률':>8s} {'같은날시장':>10s} {'리프트':>8s} "
          f"{'평균수익':>9s} {'시장':>8s} {'초과':>8s}")
    for label, mask in VARIANTS.items():
        r = measure(mask, h)
        if r is None:
            print(f"{label:24s} {'표본부족':>9s}")
            continue
        print(f"{label:24s} {r['n']:9,} {r['hit']:7.1f}% {r['base_hit']:9.1f}% "
              f"{r['lift']:+7.1f}%p {r['ret']:+8.2f}% {r['base_ret']:+7.2f}% {r['excess']:+7.2f}%p")

# ── 재료별 순수 기여: 나머지 두 조건을 고정하고 하나만 켜고 끈다 ─────────
print(f"\n{'='*98}\n재료별 순수 기여 — 나머지 두 조건을 만족한 표본 안에서 하나만 켜고 끄기\n{'='*98}")
print(f"{'재료':16s} {'지평':>5s} {'켬 N':>8s} {'끔 N':>8s} {'켬 초과':>9s} {'끔 초과':>9s} {'차이':>9s}")
for name, on_mask, off_mask in [
    ("전세난",   CR & IX & RS, (~CR) & IX & RS),
    ("매수우위", CR & IX & RS, CR & (~IX) & RS),
    ("모멘텀",   CR & IX & RS, CR & IX & (~RS)),
]:
    for h in HORIZONS:
        a, b = measure(on_mask, h), measure(off_mask, h)
        if not a or not b:
            print(f"{name:16s} {h:4d}w {'표본부족':>8s}")
            continue
        print(f"{name:16s} {h:4d}w {a['n']:8,} {b['n']:8,} "
              f"{a['excess']:+8.2f}%p {b['excess']:+8.2f}%p {a['excess']-b['excess']:+8.2f}%p")

print("\n판정 기준: '차이'가 0 근처면 그 재료는 나머지 둘 위에 아무것도 더하지 않는다.")

# ── 연도 분해 — 한 시기가 결론을 만들고 있지 않은지 ──────────────────────
# 적대적 검증에서 배운 것: STRONG_BUY 52주의 양의 초과가 2014·2016 두 코호트에 몰려 있었다.
# 전체 평균만 보고 재료를 버리면 같은 실수를 반복한다.
print(f"\n{'='*98}\n연도 분해 — 재료별 기여가 특정 시기의 산물인지 확인 (12주 기준)\n{'='*98}")


def by_year(on_mask: pd.DataFrame, off_mask: pd.DataFrame, h: int = 12) -> dict:
    f = FWD[h]
    out = {}
    for year in sorted({d.year for d in f.index}):
        rows = [d for d in f.index if d.year == year]
        sub = f.loc[rows]
        res = {}
        for tag, mask in (("on", on_mask), ("off", off_mask)):
            m = mask.loc[rows].reindex(columns=f.columns).fillna(False) & sub.notna()
            n = int(m.values.sum())
            if n < 30:
                res[tag] = None
                continue
            vals = sub.where(m)
            per_row = m.sum(axis=1)
            w = per_row / per_row.sum()
            res[tag] = (n, float(vals.stack().mean()) - float((MKT_RET[h].loc[rows] * w).sum()))
        if res["on"] and res["off"]:
            out[year] = (res["on"][0], res["on"][1], res["off"][0], res["off"][1],
                         res["on"][1] - res["off"][1])
    return out


for name, on_mask, off_mask in [
    ("전세난",   CR & IX & RS, (~CR) & IX & RS),
    ("매수우위", CR & IX & RS, CR & (~IX) & RS),
    ("모멘텀",   CR & IX & RS, CR & IX & (~RS)),
]:
    ys = by_year(on_mask, off_mask)
    if not ys:
        print(f"\n[{name}] 연도별 표본 부족")
        continue
    pos = sum(1 for v in ys.values() if v[4] > 0)
    print(f"\n[{name}] 비교 가능 {len(ys)}개 연도 · 기여 양수 {pos}개 / 음수 {len(ys)-pos}개")
    print("   " + "  ".join(f"{y}:{v[4]:+.2f}" for y, v in sorted(ys.items())))

print("\n주의: 표본은 (지역×주) 라 독립 관측이 아니다 — 117개 중 92개가 광역 지표를 상속한다.")
print("     연도별 부호가 갈리면 전체 평균 하나로 재료를 버리면 안 된다.")
