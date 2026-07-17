"""SignalConfig 버전 저장 — active + history (Phase 2).

기본값은 signals/engine.SignalConfig. 운영 적용은 수동 승인만.
"""

from __future__ import annotations

import time
from dataclasses import asdict, fields

from realty_signal import db
from realty_signal.signals.engine import SignalConfig

ACTIVE_KEY = "signal_config_active"
HISTORY_KEY = "signal_config_history"
DEFAULT_VERSION = "v1"


def _valid_keys() -> set[str]:
    return {f.name for f in fields(SignalConfig)}


def config_from_dict(d: dict) -> SignalConfig:
    return SignalConfig(**{k: v for k, v in d.items() if k in _valid_keys()})


def config_to_dict(c: SignalConfig) -> dict:
    return asdict(c)


def active_meta() -> dict:
    raw = db.kv_get(ACTIVE_KEY)
    if not raw:
        return {"version": DEFAULT_VERSION, "params": config_to_dict(SignalConfig()), "applied_ts": None}
    return raw


def active_config() -> SignalConfig:
    raw = db.kv_get(ACTIVE_KEY)
    if raw and isinstance(raw.get("params"), dict):
        return config_from_dict(raw["params"])
    return SignalConfig()


def apply_config(version: str, params: dict, *, note: str = "") -> dict:
    """새 config 버전 적용 + history append."""
    clean = config_to_dict(config_from_dict(params))
    entry = {
        "version": version,
        "params": clean,
        "note": note,
        "applied_ts": int(time.time()),
    }
    db.kv_set(ACTIVE_KEY, entry)
    hist: list = db.kv_get(HISTORY_KEY) or []
    hist.insert(0, entry)
    db.kv_set(HISTORY_KEY, hist[:20])
    return entry


def list_history(limit: int = 10) -> list[dict]:
    return (db.kv_get(HISTORY_KEY) or [])[:limit]
