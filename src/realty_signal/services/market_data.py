"""시장 데이터 캐시 서비스 — KB / 시그널 / 국면 / 백테스트.

api.py 의 @lru_cache 헬퍼를 분리해 라우터·엔진이 공유한다.
"""

from __future__ import annotations

import json
from functools import lru_cache

from realty_signal import store
from realty_signal.signals.engine import SignalConfig, evaluate


def signal_config() -> SignalConfig:
    from realty_signal.brain.config_store import active_config
    return active_config()


@lru_cache(maxsize=1)
def kb():
    return store.load()


@lru_cache(maxsize=1)
def codes_nospace():
    return {k.replace(" ", ""): v for k, v in (kb().codes or {}).items()}


def code_of(region: str) -> str:
    if not region:
        return ""
    codes = kb().codes or {}
    return codes.get(region) or codes_nospace().get(region.replace(" ", ""), "")


@lru_cache(maxsize=1)
def regime():
    from realty_signal.signals.regime import compute_regime
    codes = json.loads(store.CODES_FILE.read_text(encoding="utf-8")) if store.CODES_FILE.exists() else {}
    return compute_regime(kb(), store.load_localities(), codes)


@lru_cache(maxsize=1)
def signals_df():
    return evaluate(kb(), signal_config(), store.load_supply(), store.load_macro(),
                    store.load_volumes(), regime())


@lru_cache(maxsize=1)
def backtest():
    from realty_signal.signals.engine import backtest_summary
    return backtest_summary(kb(), signal_config())


@lru_cache(maxsize=1)
def alert_track_record():
    """signal_changes 알림 로그를 이후 12주 가격으로 채점한 성적표 (Phase 5)."""
    from realty_signal import db
    from realty_signal.brain import alert_track

    changes = db.kv_get("signal_changes") or []
    return alert_track.track_record(kb(), changes)


def signal_map() -> dict:
    df = signals_df()
    return dict(zip(df["region"], df["signal"]))


def data_age_days() -> float | None:
    try:
        last = kb().last_date
        import datetime as _dt
        return (_dt.datetime.now() - last.to_pydatetime()).total_seconds() / 86400
    except Exception:
        return None


def clear_caches() -> None:
    kb.cache_clear()
    signals_df.cache_clear()
    regime.cache_clear()
    backtest.cache_clear()
    codes_nospace.cache_clear()
    alert_track_record.cache_clear()
