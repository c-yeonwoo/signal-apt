"""CalibrationProposal — 백테스트 기반 임계값 제안 (자동 적용 금지)."""

from __future__ import annotations

import time

from realty_signal import db
from realty_signal.brain.config_store import active_config, active_meta, config_from_dict, config_to_dict
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.signals.engine import SignalConfig, backtest_summary

PROPOSAL_KEY = "signal_config_proposal"

# 스윕 대상은 **실제로 등급을 바꾸는 파라미터만** 넣는다.
# 2026-08-17 이전에는 `demand_buy` 를 스윕했는데, 그 값은 `_classify` 의 등급식에 없고
# `[참고]` 근거 문자열에만 쓰인다 — 5개 후보를 돌려도 결과가 절대 안 바뀌는 헛돌기였다.
# (원인은 README·CLAUDE.md 가 트리거 지표를 반대로 적어 둔 것. 문서 오류가 검증 도구를 오염시켰다.)
_SWEEPS: dict[str, list] = {
    "buyeridx_strong": [60, 65, 70, 75, 80],   # 강세 1표 기준 — 등급식의 핵심
    "buyeridx_mid": [45, 50, 55],              # SELL_RISK 하한
    "jeonse_crunch": [165, 170, 175],          # 전세난 진입선
    "momentum_up": [0.03, 0.05, 0.08],         # 매매 상승 판정선
}

# 최소 개선폭(%p). 이보다 작으면 잡음으로 본다.
_MIN_DELTA = 1.0


def _lift_map(bt: dict) -> dict[str, float | None]:
    """시그널별 **초과**(적중률 − 같은 기간 시장평균).

    적중률 자체를 최적화하면 안 된다 — 임계값을 올릴수록 신호가 드물어지고
    "확실한 상승장에만 발화"하게 되어 적중률은 기계적으로 오른다. 그건 예측력이 아니다.
    base rate 대비 초과분이 시그널의 실제 기여분이다.
    """
    return {r["signal"]: r.get("초과") for r in bt.get("by_signal", []) if r.get("signal")}


def _hit_map(bt: dict) -> dict[str, float | None]:
    return {r["signal"]: r.get("적중률") for r in bt.get("by_signal", []) if r.get("signal")}


def build_proposal(kb: KBWeekly) -> dict:
    """현재 active config 대비 개선 후보 제안."""
    base_c = active_config()
    base_bt = backtest_summary(kb, base_c)
    base_hits = _hit_map(base_bt)
    base_lifts = _lift_map(base_bt)
    base_dict = config_to_dict(base_c)
    suggestions: list[dict] = []

    for param, candidates in _SWEEPS.items():
        base_val = base_dict.get(param)
        for val in candidates:
            if val == base_val:
                continue
            bt = backtest_summary(kb, SignalConfig(**{**base_dict, param: val}))
            lifts, hits = _lift_map(bt), _hit_map(bt)
            for sig in ("STRONG_BUY", "BUY"):
                old, new = base_lifts.get(sig), lifts.get(sig)
                if old is None or new is None:
                    continue
                delta = round(new - old, 1)
                if delta < _MIN_DELTA:
                    continue
                suggestions.append({
                    "param": param,
                    "from": base_val,
                    "to": val,
                    "signal": sig,
                    "lift_from": old,
                    "lift_to": new,
                    "delta_pp": delta,
                    # 적중률도 같이 보여준다 — 초과가 올라도 적중률이 떨어지면 사람이 판단할 문제다
                    "hit_rate_from": base_hits.get(sig),
                    "hit_rate_to": hits.get(sig),
                    "reason": (
                        f"{sig} 시장 대비 초과 {old}%p→{new}%p (+{delta}%p) · "
                        f"적중률 {base_hits.get(sig)}%→{hits.get(sig)}%"
                    ),
                })

    suggestions.sort(key=lambda s: s.get("delta_pp") or 0, reverse=True)
    meta = active_meta()
    return {
        "generated_at": str(kb.last_date.date()),
        "generated_ts": int(time.time()),
        "active_version": meta.get("version", "v1"),
        "objective": "시장 대비 초과(적중률 − 같은 기간 전체 지역 적중률)",
        "baseline_hits": base_hits,
        "baseline_lifts": base_lifts,
        "baseline_params": base_dict,
        "swept_params": sorted(_SWEEPS),
        "suggestions": suggestions[:12],
        "disclaimer": "제안만 생성됩니다. apply API 또는 CLI로 수동 승인 후 반영.",
    }


def save_proposal(proposal: dict) -> None:
    db.kv_set(PROPOSAL_KEY, proposal)


def load_proposal() -> dict | None:
    return db.kv_get(PROPOSAL_KEY)
