"""Alert Engine v1 — 사용자 즐겨찾기 기준 알림 규칙 평가.

규칙(기본 ON):
  - signal_upgrade: 즐겨찾기 지역 시그널 등급 변동 (전역 signal_changes 로그 필터)
  - high_timing: 즐겨찾기 지역 매물 타이밍점수 ≥ threshold
  - nbhd_change: 동네 리포트 스냅샷 2주+ diff (시그널·평단·급매 등)
"""

from __future__ import annotations

from copy import deepcopy

_SIG_RANK = {"SELL_RISK": 0, "NEUTRAL": 1, "WATCH": 2, "BUY": 3, "STRONG_BUY": 4}

DEFAULT_PREFS = {
    "signal_upgrade": True,
    "high_timing": True,
    "nbhd_change": True,
    "timing_min": 70,
}

_NBHD_ALERT_KEYS = frozenset({"시그널", "평단가", "급매", "거래량비"})


def merge_prefs(stored: dict | None) -> dict:
    out = deepcopy(DEFAULT_PREFS)
    if not stored:
        return out
    for k in DEFAULT_PREFS:
        if k in stored:
            out[k] = stored[k]
    try:
        out["timing_min"] = max(0, min(100, int(out["timing_min"])))
    except (TypeError, ValueError):
        out["timing_min"] = DEFAULT_PREFS["timing_min"]
    return out


def filter_signal_changes(changes: list[dict], fav_regions: set[str], *, upgrade_only: bool) -> list[dict]:
    out = []
    for c in changes:
        if c.get("region") not in fav_regions:
            continue
        if upgrade_only:
            fr, to = c.get("from"), c.get("to")
            delta = _SIG_RANK.get(to, 1) - _SIG_RANK.get(fr, 1)
            if delta <= 0 and to != "SELL_RISK":
                continue
        out.append({**c, "kind": "signal_upgrade"})
    return out


def high_timing_listings(
    listings: list[dict],
    fav_regions: set[str],
    *,
    timing_min: int,
) -> list[dict]:
    out = []
    for x in listings:
        region = x.get("지역") or ""
        if region not in fav_regions:
            continue
        score = x.get("타이밍점수")
        if score is None:
            score = x.get("기회도")
        if score is None or score < timing_min:
            continue
        out.append({
            "kind": "high_timing",
            "region": region,
            "name": x.get("단지명"),
            "type": x.get("유형"),
            "score": score,
            "reason": x.get("타이밍근거") or x.get("기회도근거") or "",
            "confidence": x.get("confidence"),
        })
    out.sort(key=lambda i: i.get("score") or 0, reverse=True)
    return out[:20]


def nbhd_change_items(nbhd_diffs: dict[str, list[dict]]) -> list[dict]:
    """region → diff rows → 알림 아이템."""
    out = []
    for region, diffs in nbhd_diffs.items():
        sig = [d for d in diffs if d.get("key") in _NBHD_ALERT_KEYS]
        if not sig:
            continue
        parts = []
        for d in sig[:4]:
            k, a, b = d.get("key"), d.get("from"), d.get("to")
            parts.append(f"{k} {b if b is not None else '–'}(←{a if a is not None else '–'})")
        out.append({
            "kind": "nbhd_change",
            "region": region,
            "summary": " · ".join(parts),
            "diffs": sig,
        })
    return out


def evaluate(
    fav_regions: set[str],
    prefs: dict,
    *,
    signal_changes: list[dict],
    signal_map: dict[str, str],
    listings: list[dict] | None = None,
    nbhd_diffs: dict[str, list[dict]] | None = None,
    seen_before: str = "",
) -> dict:
    """알림 페이로드 — changes / timing / nbhd / digest / unread."""
    p = merge_prefs(prefs)
    changes = filter_signal_changes(signal_changes, fav_regions, upgrade_only=True) if p["signal_upgrade"] else []
    timing = high_timing_listings(listings or [], fav_regions, timing_min=p["timing_min"]) if p["high_timing"] else []
    nbhd = nbhd_change_items(nbhd_diffs or {}) if p["nbhd_change"] else []

    digest = [{"region": r, "signal": signal_map.get(r, "")} for r in sorted(fav_regions)]

    def _fresh(d: str | None) -> bool:
        return bool(d and (not seen_before or d > seen_before))

    unread = sum(1 for c in changes if _fresh(c.get("date")))
    unread += len(timing) if seen_before else min(len(timing), 5)
    unread += len(nbhd) if not seen_before else 0

    return {
        "changes": changes[:50],
        "timing": timing,
        "nbhd": nbhd,
        "digest": digest,
        "unread": unread,
        "prefs": p,
    }
