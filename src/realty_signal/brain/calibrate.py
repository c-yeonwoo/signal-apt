"""CalibrationProposal — 백테스트 기반 임계값 제안 (자동 적용 금지)."""

from __future__ import annotations

import time

from realty_signal import db
from realty_signal.brain.config_store import active_config, active_meta, config_from_dict, config_to_dict
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.signals.engine import SignalConfig, backtest_summary

PROPOSAL_KEY = "signal_config_proposal"

_SWEEP_DEMAND_BUY = [15, 18, 20, 22, 25]
_SWEEP_JEONSE_CRUNCH = [165, 170, 175]


def _hit_map(bt: dict) -> dict[str, float | None]:
    return {r["signal"]: r.get("적중률") for r in bt.get("by_signal", []) if r.get("signal")}


def build_proposal(kb: KBWeekly) -> dict:
    """현재 active config 대비 개선 후보 제안."""
    base_c = active_config()
    base_bt = backtest_summary(kb, base_c)
    base_hits = _hit_map(base_bt)
    suggestions: list[dict] = []

    def _try(param: str, base_val, candidates: list, factory):
        for val in candidates:
            if val == base_val:
                continue
            c = factory(val)
            hits = _hit_map(backtest_summary(kb, c))
            for sig in ("STRONG_BUY", "BUY"):
                old, new = base_hits.get(sig), hits.get(sig)
                if old is None or new is None:
                    continue
                delta = round(new - old, 1)
                if delta >= 1.0:
                    suggestions.append({
                        "param": param,
                        "from": base_val,
                        "to": val,
                        "signal": sig,
                        "hit_rate_from": old,
                        "hit_rate_to": new,
                        "delta_pp": delta,
                        "reason": f"{sig} 적중률 {old}%→{new}% (+{delta}%p)",
                    })

    _try("demand_buy", base_c.demand_buy, _SWEEP_DEMAND_BUY,
         lambda v: SignalConfig(**{**config_to_dict(base_c), "demand_buy": v}))
    _try("jeonse_crunch", base_c.jeonse_crunch, _SWEEP_JEONSE_CRUNCH,
         lambda v: SignalConfig(**{**config_to_dict(base_c), "jeonse_crunch": v}))

    suggestions.sort(key=lambda s: s.get("delta_pp") or 0, reverse=True)
    meta = active_meta()
    return {
        "generated_at": str(kb.last_date.date()),
        "generated_ts": int(time.time()),
        "active_version": meta.get("version", "v1"),
        "baseline_hits": base_hits,
        "baseline_params": config_to_dict(base_c),
        "suggestions": suggestions[:12],
        "disclaimer": "제안만 생성됩니다. apply API 또는 CLI로 수동 승인 후 반영.",
    }


def save_proposal(proposal: dict) -> None:
    db.kv_set(PROPOSAL_KEY, proposal)


def load_proposal() -> dict | None:
    return db.kv_get(PROPOSAL_KEY)
