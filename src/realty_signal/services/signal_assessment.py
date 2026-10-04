"""Evidence-linked market labels. Legacy grades remain available for audit."""

from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import math
import time

import pandas as pd

from realty_signal import db
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.signals.engine import SignalConfig
from realty_signal.time_kst import today_kst

VERSION = "signal-assessment-v1"
GUARD_VERSION = "source-and-price-v3"
MAX_OBSERVATION_AGE_DAYS = 8
INCHEON_RETIRED_CODES = frozenset({"28110", "28140", "28170", "28260"})
LABELS = {"STRONG_BUY": "강력매수", "BUY": "매수", "WATCH": "관망",
          "NEUTRAL": "중립", "SELL_RISK": "매도주의"}


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _last_four(kb: KBWeekly, region: str, asof: date) -> tuple[list[dict], bool]:
    series = kb.series(region, "sale_change")
    if series.empty:
        return [], False
    series = series[series.index <= pd.Timestamp(asof)].tail(4)
    items = [{"date": str(stamp.date()), "value": _finite(value)} for stamp, value in series.items()]
    if len(items) != 4 or any(item["value"] is None for item in items):
        return items, False
    days = [pd.Timestamp(item["date"]) for item in items]
    return items, days[-1].date() == asof and all((b - a).days == 7 for a, b in zip(days, days[1:]))


def _metric(reason_id: str, label: str, value: float | None, unit: str,
            threshold: float, passing: bool, source: str, role: str = "driver") -> dict:
    return {"reason_id": reason_id, "label": label, "value": value, "unit": unit,
            "threshold": threshold, "passing": passing, "role": role,
            "source_region": source, "inherited": False}


def _held_summary(flags: list[str], asof: date, today: date) -> str:
    if "region_boundary_obsolete" in flags:
        return ("2026-07 인천 행정구역 개편 전 권역의 통계입니다. 새 구역으로 과거 값을 임의 분할하지 않으며, "
                "현재 매수·매도 판정으로 사용하지 않습니다.")
    if "source_stale" in flags:
        return (f"KB {asof.isoformat()} 관측 후 {(today - asof).days}일이 지났습니다. "
                "새 기준일을 확인하기 전에는 과거 매수·매도 등급을 현재 판단으로 사용하지 않습니다.")
    if "region_identity_ambiguous" in flags:
        return "같은 이름의 다른 지역과 자료 출처를 구분할 수 없어 지역 판정을 보류합니다."
    if "market_inputs_missing" in flags or "market_inputs_stale" in flags:
        return "전세수급·매수심리 자료가 없거나 가격 기준일과 맞지 않아 지역 판정을 보류합니다."
    if "sale_weeks_incomplete" in flags:
        return "최근 4주 가격 자료가 이어지지 않아 지역 판정을 보류합니다."
    if "price_direction_conflict" in flags:
        return "기존 매수 규칙과 최근 가격 하락이 충돌해 지역 판정을 보류합니다."
    return "자료 또는 가격 방향을 확인한 뒤 판단합니다."


def build(kb: KBWeekly, row: dict, config: SignalConfig, *,
          asof: date | None = None, today: date | None = None) -> dict:
    """Create a deterministic assessment without reading or writing issuance history."""
    asof = asof or kb.last_date.date()
    today = today or today_kst()
    region = str(row["region"])
    code = str((kb.codes or {}).get(region) or "")
    boundary_obsolete = today >= date(2026, 7, 1) and code[:5] in INCHEON_RETIRED_CODES
    # 이름만 보존한 과거 캐시는 코드가 있어도 어느 중구의 가격 행인지 증명하지 못한다.
    collision_sensitive = region in {"중구", "인천 중구"}
    parent_conflict = ((region == "중구" and not code.startswith("11"))
                       or (region == "인천 중구" and not code.startswith("28")))
    ambiguous = parent_conflict or (collision_sensitive and not kb.identity_verified)
    region_id = (f"ambiguous:{region}" if ambiguous else
                 f"kb:{code}" if code and kb.identity_verified else f"unverified:{region}")
    weeks, complete = _last_four(kb, region, asof)
    momentum = round(sum(x["value"] for x in weeks) / 4, 4) if complete else None
    js, bs = _finite(row.get("전세수급")), _finite(row.get("매수우위지수"))
    source = str(row.get("수급출처") or region)
    input_dates_current = all(
        not kb.series(source, metric).empty
        and kb.series(source, metric).index[-1].date() == asof
        for metric in ("jeonse_supply", "buyer_superiority")
    )
    reasons = [
        _metric("jeonse_pressure", "전세수급 압력", js, "지수", config.jeonse_crunch,
                js is not None and js >= config.jeonse_crunch, source),
        _metric("buyer_interest", "매수심리의 자체 관찰선", bs, "지수", config.buyeridx_strong,
                bs is not None and bs >= config.buyeridx_strong, source),
        _metric("sale_momentum", "최근 4주 주간 매매변동률 평균", momentum, "%/주", config.momentum_up,
                momentum is not None and momentum >= config.momentum_up, region),
    ]
    for reason in reasons[:2]:
        reason["inherited"] = source != region
    if bs is not None and bs < 100:
        reasons.append({"reason_id": "buyer_balance", "role": "limitation",
                        "label": "KB 응답 균형선 100 미만", "value": bs, "unit": "지수",
                        "threshold": 100, "passing": False, "source_region": source,
                        "inherited": source != region})
    risk_flags = []
    if not complete:
        risk_flags.append("sale_weeks_incomplete")
    if js is None or bs is None:
        risk_flags.append("market_inputs_missing")
    elif not input_dates_current:
        risk_flags.append("market_inputs_stale")
    if ambiguous:
        risk_flags.append("region_identity_ambiguous")
    if boundary_obsolete:
        risk_flags.append("region_boundary_obsolete")
    if (today - asof).days > MAX_OBSERVATION_AGE_DAYS:
        risk_flags.append("source_stale")
    raw = str(row.get("signal") or "NEUTRAL")
    if raw in {"BUY", "STRONG_BUY"} and momentum is not None and momentum < 0:
        risk_flags.append("price_direction_conflict")
    if momentum is not None and momentum < 0:
        reasons.append({"reason_id": "price_direction_conflict", "role": "counterevidence",
                        "label": "최근 4주 주간 매매변동률 평균은 음수입니다",
                        "value": momentum, "unit": "%/주", "source_region": region})
    supply = _finite(row.get("공급압력"))
    if supply is not None and supply >= config.supply_glut:
        reasons.append(_metric("supply_pressure", "입주물량 부담", supply, "배",
                               config.supply_glut, True, region, "counterevidence"))
    overlay = row.get("매도보정")
    if raw == "SELL_RISK" and isinstance(overlay, dict) and overlay.get("applied"):
        for factor in overlay.get("factors") or []:
            if not isinstance(factor, dict) or factor.get("id") in {"sale_decline", "supply_pressure"}:
                continue  # 가격 하락·공급 부담은 위의 관측 근거와 중복 표시하지 않는다.
            factor_id = factor.get("id")
            if factor_id not in {"transaction_volume_low", "lower_tier_surge",
                                 "regional_cycle_late", "national_rate_rising"}:
                continue
            reasons.append({"reason_id": factor_id, "role": "counterevidence",
                            "label": factor["label"], "value": factor.get("value"),
                            "unit": factor.get("unit"), "passing": True,
                            "weight": factor.get("weight"),
                            "source_region": "전국" if factor_id == "national_rate_rising" else region,
                            "inherited": False})
    status = "held" if risk_flags else "ready"
    config_hash = _hash(asdict(config))
    basis = {"version": VERSION, "guard_version": GUARD_VERSION, "region_id": region_id,
             "asof": asof.isoformat(), "config_hash": config_hash, "raw_grade": raw,
             "reasons": reasons, "price_weeks": weeks, "risk_flags": risk_flags}
    if raw == "SELL_RISK" and isinstance(overlay, dict) and overlay.get("applied"):
        basis["sell_overlay"] = {"score": overlay.get("score"),
                                 "threshold": overlay.get("threshold"),
                                 "factor_ids": [factor.get("id") for factor in overlay.get("factors") or []
                                                if isinstance(factor, dict)]}
    sell_cautions = [reason["label"] for reason in reasons
                     if reason["role"] == "counterevidence"]
    ready_summary = (f"지역 시장 신호는 매도주의입니다. 주요 근거: {' · '.join(sell_cautions[:3])}. "
                     "개별 매도 지시나 미래 가격 하락 확률은 아닙니다."
                     if raw == "SELL_RISK" and sell_cautions else
                     f"지역 시장 신호는 {LABELS.get(raw, '판단 보류')}입니다. "
                     "개별 매물의 적정 가격이나 미래 수익을 뜻하지 않습니다.")
    return {**basis, "assessment_id": _hash(basis), "region": region,
            "display_grade": LABELS.get(raw, "판단 보류") if status == "ready" else "판단 보류",
            "assessment_status": status,
            "summary": _held_summary(risk_flags, asof, today) if status == "held" else ready_summary,
            "scope_note": (f"전세수급과 매수심리는 {source} 권역 자료를 함께 사용합니다."
                           if source != region else "이 지역 자료를 사용합니다."),
            "change": {"type": "first_observation", "previous_grade": None, "changed_reasons": []}}


def _previous(c, region_id: str, asof: str, current_id: str) -> dict | None:
    hit = c.execute("SELECT data FROM signal_assessments WHERE region_id=? AND asof<=? AND id<>? "
                    "ORDER BY asof DESC, issued_at DESC, id DESC LIMIT 1", (region_id, asof, current_id)).fetchone()
    return json.loads(hit[0]) if hit else None


def with_previous(assessment: dict) -> dict:
    c = db.conn()
    try:
        previous = _previous(c, assessment["region_id"], assessment["asof"], assessment["assessment_id"])
    finally:
        c.close()
    if previous is None:
        return assessment
    current = deepcopy(assessment)
    old_reasons = {r["reason_id"]: r for r in previous.get("reasons", [])}
    for reason in current["reasons"]:
        prior = old_reasons.get(reason["reason_id"])
        if prior:
            reason["previous_value"] = prior.get("value")
            reason["previous_passing"] = prior.get("passing")
    changed = [r["reason_id"] for r in current["reasons"]
               if r.get("value") != (old_reasons.get(r["reason_id"]) or {}).get("value")
               or r.get("passing") != (old_reasons.get(r["reason_id"]) or {}).get("passing")]
    grade_changed = current["raw_grade"] != previous.get("raw_grade")
    if grade_changed:
        changed.append("grade")
    safety_changed = (current["assessment_status"] != previous.get("assessment_status")
                      or current["risk_flags"] != previous.get("risk_flags"))
    if safety_changed:
        changed.append("safety_status")
    method_changed = any(current[k] != previous.get(k) for k in ("version", "config_hash", "guard_version"))
    same_asof = current["asof"] == previous.get("asof")
    freshness_only = (same_asof and set(changed) == {"safety_status"}
                      and (set(current["risk_flags"]) ^ set(previous.get("risk_flags") or [])) == {"source_stale"})
    explanation_only = (same_asof and not method_changed and not grade_changed and not safety_changed
                        and previous.get("sell_overlay") is None and current.get("sell_overlay") is not None
                        and all(reason["reason_id"] not in old_reasons for reason in current["reasons"]
                                if reason["reason_id"] in changed))
    current["change"] = {
        "type": "mixed_change" if method_changed and changed else "method_change" if method_changed
                else "freshness_change" if freshness_only else "explanation_change" if explanation_only
                else "source_revision" if same_asof and changed
                else "market_change" if changed else "unchanged",
        "previous_grade": previous.get("display_grade"), "changed_reasons": changed,
        "previous_assessment_id": previous.get("assessment_id"),
    }
    return current


def issue_many(assessments: list[dict]) -> int:
    """Append only the assessments actually issued by the refresh pipeline."""
    c = db.conn()
    try:
        issued_at = time.time_ns()
        rows = [(item["assessment_id"], item["region_id"], item["region"], item["asof"],
                 issued_at, json.dumps(item, ensure_ascii=False, allow_nan=False)) for item in assessments]
        c.executemany("INSERT OR IGNORE INTO signal_assessments VALUES(?,?,?,?,?,?)", rows)
        c.commit()
        return c.total_changes
    finally:
        c.close()
