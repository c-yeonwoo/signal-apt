"""반박 전담 검증 — 주장 1/2/3 재계산."""
import random, statistics as st, sys
import numpy as np
import pandas as pd

from realty_signal.services import market_data as md
from realty_signal.signals.engine import price_index_from, signal_history

kb = md.kb()
cfg = md.signal_config()
regions_all = list(kb.latest().index)

idx_of = {}
for r in regions_all:
    s = kb.series(r, "sale_change")
    if not s.empty:
        idx_of[r] = price_index_from(s)
regions = list(idx_of)

# ── 공통: 가격지수 매트릭스 + 전방수익 매트릭스 ─────────────────────────
px = pd.DataFrame({r: idx_of[r] for r in regions}).sort_index()
H = [12, 26, 52]
FWD = {h: (px.shift(-h) / px - 1) * 100 for h in H}

# ── 부모(광역) 그룹 ─────────────────────────────────────────────────────
codes = kb.codes or {}
AGG = {"전국", "수도권", "6개광역시", "5개광역시", "기타지방", "강북14개구", "강남11개구"}
def grp(r):
    c = (codes.get(r) or "")
    return c[:2] if c else "??"
groups = {}
for r in regions:
    groups.setdefault(grp(r), []).append(r)
print("=== 광역 그룹 크기 ===", {k: len(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))})

# ── 시그널 구간 수집 (한 번만) ──────────────────────────────────────────
IV = {"STRONG_BUY": [], "BUY": [], "SELL": []}
for r in regions_all:
    try:
        for iv in signal_history(kb, r, cfg):
            t = iv.get("signal")
            if t in IV:
                IV[t].append({"region": r, "start": pd.Timestamp(iv["start"]),
                              "end": pd.Timestamp(iv["end"]),
                              "after": iv.get("after12w_pct")})
    except Exception:
        pass
print("구간 수:", {t: len(v) for t, v in IV.items()})

# ══════════════════════════════════════════════════════════════════════
# 주장 1 — 순열검정
# ══════════════════════════════════════════════════════════════════════
print("\n########## 주장 1: 순열검정 ##########")

def forward_at(region, e):
    """verify_backtest.forward_at 원본 로직 그대로."""
    idx = idx_of.get(region)
    if idx is None:
        return None
    i1 = idx.asof(e)
    if pd.isna(i1) or not i1:
        return None
    after = idx[idx.index > e]
    if not len(after):
        return None
    a = after.iloc[min(11, len(after) - 1)]
    return None if pd.isna(a) else round((a / i1 - 1) * 100, 1)

real = {}
for t, ivs in IV.items():
    ev = [x for x in ivs if x["after"] is not None]
    hit = sum(1 for x in ev if ((x["after"] > 0) if t != "SELL" else (x["after"] <= 0)))
    real[t] = hit / len(ev) * 100
    # 재계산 forward_at 과 원본 after12w_pct 가 같은지 대조
    mism = sum(1 for x in ev if forward_at(x["region"], x["end"]) != x["after"])
    print(f"{t:11s} 실제 {real[t]:5.1f}% (N={len(ev)})  forward_at 불일치 {mism}건")

# 잘림(truncation) 진단: 12주 미만 남은 구간이 몇 개인가
trunc = {}
for t, ivs in IV.items():
    n = 0
    for x in ivs:
        idx = idx_of[x["region"]]
        if len(idx[idx.index > x["end"]]) < 12:
            n += 1
    trunc[t] = n
print("12주 미만 잘린 평가:", trunc)

def perm(t, ivs, seed, mode, trials=200):
    rng = random.Random(seed)
    ev = [x for x in ivs if x["after"] is not None]
    rates = []
    for _ in range(trials):
        h = c = 0
        for x in ev:
            if mode == "any":
                r = regions[rng.randrange(len(regions))]
            elif mode == "cross":       # 같은 광역 제외 (독립성 강화)
                g = grp(x["region"])
                pool = [q for q in regions if grp(q) != g]
                r = pool[rng.randrange(len(pool))]
            elif mode == "within":      # 같은 광역 안에서만 (상속 공유 통제)
                pool = [q for q in groups.get(grp(x["region"]), regions) if q != x["region"]] or regions
                r = pool[rng.randrange(len(pool))]
            f = forward_at(r, x["end"])
            if f is None:
                continue
            c += 1
            if (f > 0) if t != "SELL" else (f <= 0):
                h += 1
        if c:
            rates.append(h / c * 100)
    rates.sort()
    return st.mean(rates), rates[int(.05 * len(rates))], rates[int(.95 * len(rates))]

print(f"\n{'시그널':11s} {'모드':7s} {'seed':>10s} {'순열평균':>9s} {'5~95%':>15s} {'리프트':>8s}")
for t, ivs in IV.items():
    for mode in ("any", "cross", "within"):
        for seed in (20260817, 1, 999):
            if mode != "any" and seed != 20260817:
                continue
            m, lo, hi = perm(t, ivs, seed, mode)
            print(f"{t:11s} {mode:7s} {seed:10d} {m:8.1f}% {lo:6.1f}~{hi:6.1f}% {real[t]-m:+7.1f}%p")

# 모멘텀-only 플라시보: '직전 4주 평균 상승' 만으로 같은 개수 표본 뽑기
print("\n--- 플라시보: 모멘텀만(4주 평균>=0.02) 필터, STRONG_BUY 와 같은 판정 ---")
mom_hits = mom_n = 0
for r in regions:
    s = kb.series(r, "sale_change")
    if s.empty:
        continue
    m4 = s.rolling(cfg.momentum_weeks).mean()
    idx = idx_of[r]
    for d, v in m4.items():
        if pd.isna(v) or v < cfg.momentum_up:
            continue
        f = forward_at(r, d)
        if f is None:
            continue
        mom_n += 1
        mom_hits += f > 0
print(f"모멘텀-only 필터: N={mom_n:,}  12주 상승률 {mom_hits/mom_n*100:.1f}%")

# ══════════════════════════════════════════════════════════════════════
# 주장 2 — 온셋 초과수익 · 벤치마크 교체 · 연도분해
# ══════════════════════════════════════════════════════════════════════
print("\n########## 주장 2: 벤치마크 ##########")
nat = {h: FWD[h].mean(axis=1) for h in H}                   # 전국 평균(현행)
gsum, gcnt = {}, {}
for h in H:
    f = FWD[h]
    gsum[h], gcnt[h] = {}, {}
    for g, rs in groups.items():
        sub = f[[r for r in rs if r in f.columns]]
        gsum[h][g] = sub.sum(axis=1, min_count=1)
        gcnt[h][g] = sub.notna().sum(axis=1)

def bench(kind, region, date, h):
    try:
        if kind == "nat":
            v = nat[h].get(date)
        else:
            g = grp(region)
            s, c = gsum[h][g].get(date), gcnt[h][g].get(date)
            if s is None or c is None or c <= 1:
                return None
            own = FWD[h][region].get(date)
            if pd.isna(own):
                return None
            v = (s - own) / (c - 1)          # leave-one-out 같은 광역 peer
        return None if v is None or pd.isna(v) else float(v)
    except KeyError:
        return None

print(f"{'시그널':11s} {'h':>4s} {'N':>6s} {'평균수익':>9s} {'전국벤치':>9s} {'초과(전국)':>10s} "
      f"{'광역peer':>9s} {'초과(peer)':>11s}")
res2 = {}
for t, ivs in IV.items():
    for h in H:
        rows = []
        for x in ivs:
            r, d = x["region"], x["start"]
            if r not in FWD[h].columns or d not in FWD[h].index:
                continue
            ret = FWD[h][r].get(d)
            if pd.isna(ret):
                continue
            bn, bp_ = bench("nat", r, d, h), bench("grp", r, d, h)
            if bn is None or bp_ is None:
                continue
            rows.append((d.year, float(ret), bn, bp_))
        if not rows:
            continue
        res2[(t, h)] = rows
        print(f"{t:11s} {h:3d}w {len(rows):6,} {np.mean([x[1] for x in rows]):+8.2f}% "
              f"{np.mean([x[2] for x in rows]):+8.2f}% {np.mean([x[1]-x[2] for x in rows]):+9.2f}%p "
              f"{np.mean([x[3] for x in rows]):+8.2f}% {np.mean([x[1]-x[3] for x in rows]):+10.2f}%p")

print("\n--- 생존편향: 52주 온셋 초과수익 연도별 (STRONG_BUY / BUY) ---")
for t in ("STRONG_BUY", "BUY", "SELL"):
    rows = res2.get((t, 52), [])
    by = {}
    for y, r, bn, bp_ in rows:
        by.setdefault(y, []).append((r - bn, r - bp_))
    print(f"[{t}]")
    for y in sorted(by):
        v = by[y]
        print(f"  {y}  N={len(v):5,}  초과(전국) {np.mean([a for a,_ in v]):+6.2f}%p  "
              f"초과(peer) {np.mean([b for _,b in v]):+6.2f}%p")

print("\n--- 잘려나간(52주 미확보) 온셋의 연도 분포 ---")
for t in ("STRONG_BUY", "BUY"):
    have = {(x[0]) for x in []}
    cut = {}
    for x in IV[t]:
        r, d = x["region"], x["start"]
        ok = (r in FWD[52].columns and d in FWD[52].index and not pd.isna(FWD[52][r].get(d)))
        if not ok:
            cut[d.year] = cut.get(d.year, 0) + 1
    print(f"{t}: {dict(sorted(cut.items()))}")

# ══════════════════════════════════════════════════════════════════════
# 주장 3 — AR(1) 자기상관 시뮬레이션
# ══════════════════════════════════════════════════════════════════════
print("\n########## 주장 3: AR(1) 시뮬레이션 ##########")
acs, params = [], {}
for r in regions:
    s = kb.series(r, "sale_change").dropna()
    if len(s) < 100:
        continue
    a1 = s.autocorr(1)
    acs.append(a1)
    params[r] = (float(s.mean()), float(s.std()), float(a1), len(s), s.index)
print(f"주간 매매증감 lag-1 자기상관: 중앙값 {np.median(acs):.3f}  "
      f"평균 {np.mean(acs):.3f}  5~95% {np.percentile(acs,5):.3f}~{np.percentile(acs,95):.3f}")

class FakeKB:
    """sale_change 만 AR(1) 합성으로 대체. 전세수급·매수우위는 실제값 유지."""
    def __init__(self, real, sim):
        self._r, self._s = real, sim
        self.codes, self.last_date = real.codes, real.last_date
    def latest(self):
        return self._r.latest()
    def series(self, region, col):
        if col == "sale_change" and region in self._s:
            return self._s[region]
        return self._r.series(region, col)

def run_sim(seed):
    rs = np.random.default_rng(seed)
    sim = {}
    for r, (mu, sd, rho, n, ix) in params.items():
        rho = float(np.clip(rho, -0.95, 0.95))
        eps = rs.normal(0, sd * np.sqrt(1 - rho ** 2), n)
        x = np.empty(n); x[0] = mu + eps[0]
        for i in range(1, n):
            x[i] = mu + rho * (x[i - 1] - mu) + eps[i]
        sim[r] = pd.Series(x, index=ix)
    fk = FakeKB(kb, sim)
    out = {}
    fidx = {r: price_index_from(fk.series(r, "sale_change")) for r in sim}
    for r in regions_all:
        try:
            ivs = signal_history(fk, r, cfg)
        except Exception:
            continue
        for iv in ivs:
            t = iv.get("signal")
            if t not in ("STRONG_BUY", "BUY", "SELL") or iv.get("after12w_pct") is None:
                continue
            a = out.setdefault(t, [0, 0, []])
            a[1] += 1
            v = iv["after12w_pct"]
            a[2].append(v)
            if (v > 0) if t != "SELL" else (v <= 0):
                a[0] += 1
        # 온셋 12주 상승률 (99.3% 대조)
        for iv in ivs:
            t = iv.get("signal")
            if t != "STRONG_BUY" or r not in fidx:
                continue
            ix = fidx[r]; s0 = pd.Timestamp(iv["start"])
            i0 = ix.asof(s0)
            aft = ix[ix.index > s0]
            if pd.isna(i0) or not i0 or len(aft) < 12:
                continue
            b = out.setdefault("SB_ONSET12", [0, 0, []])
            b[1] += 1
            v = (aft.iloc[11] / i0 - 1) * 100
            b[2].append(v)
            if v > 0:
                b[0] += 1
    return out

for seed in (7, 42):
    o = run_sim(seed)
    print(f"[seed {seed}]", "  ".join(
        f"{t}: {v[0]/v[1]*100:.1f}% (N={v[1]:,}, 평균 {np.mean(v[2]):+.2f}%)"
        for t, v in sorted(o.items())))

# 실제 온셋 12주 상승률 (대조군)
sb = IV["STRONG_BUY"]
vals = [float(FWD[12][x["region"]].get(x["start"])) for x in sb
        if x["region"] in FWD[12].columns and x["start"] in FWD[12].index
        and not pd.isna(FWD[12][x["region"]].get(x["start"]))]
print(f"[실제] STRONG_BUY 온셋 12주 상승률 {sum(1 for v in vals if v>0)/len(vals)*100:.1f}% (N={len(vals)})")
