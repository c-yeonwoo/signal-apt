"""Frozen, purged chronological evaluation protocol. No automatic promotion.

Input records must come from point-in-time evidence, not reconstructed charts.
Calendar blocks and region clusters are jointly resampled for dependent returns.
This is a research harness, not a claim of verified investment performance.
"""
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone, timedelta
from hashlib import sha256
import json
import math
import random
from statistics import mean

from realty_signal import db


def params_hash(params):
    return sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Protocol:
    train_end: str
    validation_end: str
    test_end: str
    horizon_weeks: int = 12
    block_weeks: int = 13
    min_samples: int = 30
    min_time_blocks: int = 8
    min_region_clusters: int = 3
    version: str = "purged-cluster-v1"

    def __post_init__(self):
        if not date.fromisoformat(self.train_end) < date.fromisoformat(self.validation_end) < date.fromisoformat(self.test_end):
            raise ValueError("chronological_boundaries_required")
        if self.block_weeks < self.horizon_weeks or self.horizon_weeks < 1:
            raise ValueError("block_must_cover_label_horizon")
        if self.min_samples < 30 or self.min_time_blocks < 8 or self.min_region_clusters < 3:
            raise ValueError("minimum_evidence_thresholds_required")


def evaluate(records, protocol: Protocol, *, params: dict, seed=0):
    boundaries = [date.fromisoformat(x) for x in (protocol.train_end, protocol.validation_end, protocol.test_end)]
    horizon = timedelta(weeks=protocol.horizon_weeks)
    samples, excluded = [], Counter()
    counts = Counter()
    fingerprint = params_hash(params)
    seen = set()
    for row in records:
        try:
            decision_time = datetime.fromisoformat(row["decision_at"].replace("Z", "+00:00"))
            available_time = datetime.fromisoformat(row["available_at"].replace("Z", "+00:00"))
            if decision_time.tzinfo is None:
                decision_time = decision_time.replace(tzinfo=timezone.utc)
            if available_time.tzinfo is None:
                available_time = available_time.replace(tzinfo=timezone.utc)
            at = date.fromisoformat(row["decision_at"][:10])
            result, baseline = float(row["return"]), float(row["baseline_return"])
            if not row.get("vintage_verified") or not row.get("evidence_id") or row.get("config_hash") != fingerprint:
                excluded["unverified_vintage_or_config"] += 1
                continue
            identity = (row["decision_at"], row["region_cluster"])
            if identity in seen:
                excluded["duplicate_decision"] += 1
                continue
            seen.add(identity)
            if available_time > decision_time:
                excluded["future_information"] += 1
                continue
            if at+horizon > boundaries[2] or date.fromisoformat(row["outcome_at"][:10]) != at+horizon:
                excluded["immature_or_wrong_horizon"] += 1
                continue
            if not math.isfinite(result) or not math.isfinite(baseline) or not row.get("region_cluster"):
                raise ValueError("missing_result_or_cluster")
            if at <= boundaries[0]-horizon:
                counts["train"] += 1
            elif boundaries[0] < at <= boundaries[1]-horizon:
                counts["validation"] += 1
            elif boundaries[1] < at <= boundaries[2]-horizon:
                counts["test"] += 1
                samples.append((at.toordinal()//(7*protocol.block_weeks), row["region_cluster"], result-baseline, result))
            else:
                excluded["purged_boundary"] += 1
        except (KeyError, TypeError, ValueError):
            excluded["missing_evidence"] += 1
    blocks = sorted({x[0] for x in samples})
    clusters = sorted({x[1] for x in samples})
    sufficient = (len(samples) >= protocol.min_samples and len(blocks) >= protocol.min_time_blocks
                  and len(clusters) >= protocol.min_region_clusters and counts["train"] > 0 and counts["validation"] > 0)
    interval = None
    if sufficient:
        rng, draws = random.Random(seed), []
        for _ in range(1000):
            bw = Counter(rng.choices(blocks, k=len(blocks)))
            rw = Counter(rng.choices(clusters, k=len(clusters)))
            weighted = [(x[2], bw[x[0]]*rw[x[1]]) for x in samples]
            mass = sum(w for _, w in weighted)
            if mass:
                draws.append(sum(v*w for v, w in weighted)/mass)
        draws.sort()
        interval = [draws[int(.025*len(draws))], draws[min(len(draws)-1, int(.975*len(draws)))]] if draws else None
    return {"protocol": asdict(protocol), "config_hash": fingerprint, "counts": dict(counts),
            "excluded": dict(excluded), "time_blocks": len(blocks), "region_clusters": len(clusters),
            "mean_excess": mean(x[2] for x in samples) if samples else None,
            "downside_rate": mean(x[3] < 0 for x in samples) if samples else None,
            "excess_ci95": interval, "sufficient": sufficient,
            "promotable": bool(sufficient and interval and interval[0] > 0
                               and not any(n for k,n in excluded.items() if k != "purged_boundary")),
            "note": "표본·보류셋·원본 시점 검증을 모두 충족해야 수동 승격 가능. 승격은 자동 실행되지 않음."}


def walk_forward(records, protocols, *, params):
    """Expanding train / validation / untouched test folds with fixed parameters."""
    return [evaluate(records, protocol, params=params) for protocol in protocols]


def save_result(result):
    identity = sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    db.kv_set(f"evaluation:{identity}", result)
    return identity


def require_promotion(validation_id, params):
    result = db.kv_get(f"evaluation:{validation_id}") if validation_id else None
    if not result or not result.get("promotable") or result.get("config_hash") != params_hash(params):
        raise ValueError("승격 가능한 시점외 검증 결과와 동일한 파라미터가 필요합니다")
