"""유저가 본 후보의 보류 이유를 주 단위로 남긴다.

브리핑 스냅샷과 분리한다. 발송이 홈의 이번 주 변화를 지우면 안 된다.
"""

from __future__ import annotations

from datetime import date, timedelta

from realty_signal import db
from realty_signal.briefing import today_kst


def week_key(day: date | None = None) -> str:
    d = day or today_kst()
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def remember(uid: int, rows: list[dict]) -> int:
    """이번 주 키로 덮어쓴다. 반환은 저장한 후보 수."""
    if not uid:
        return 0
    week = week_key()
    saved = 0
    for row in rows or []:
        evidence = (row.get("evidence") or [{}])[0]
        entity_id = evidence.get("entity_id")
        if not entity_id:
            continue
        decision = row.get("decision") or {}
        db.decision_snap_put(uid, entity_id, week, {
            "name": row.get("단지명") or row.get("단지") or "후보",
            "region": row.get("지역") or row.get("region") or "",
            "feasibility": decision.get("feasibility"),
            "unknowns": list(decision.get("unknowns") or []),
            "lines": row.get("lines") or {},
        })
        saved += 1
    return saved


def changes(uid: int, *, today: date | None = None) -> dict:
    """직전 저장 주와 이번 주의 미확인·현금·가격 문장을 비교한다."""
    current = week_key(today)
    older = [w for w in db.decision_snap_weeks(uid) if w < current]
    if not older:
        return {"ready": True, "week": current, "prev_week": None, "items": [],
                "unchanged": 0, "reason": "지난주 보류 기록이 없습니다"}
    prev = older[-1]
    before = {r["entity_id"]: r for r in db.decision_snap_week(uid, prev)}
    after = {r["entity_id"]: r for r in db.decision_snap_week(uid, current)}
    if not after:
        return {"ready": True, "week": current, "prev_week": prev, "items": [],
                "unchanged": 0, "reason": "이번 주 기록한 후보가 없습니다"}
    items = []
    unchanged = 0
    for entity_id, row in after.items():
        old = before.get(entity_id)
        if old is None:
            items.append({"name": row.get("name"), "region": row.get("region"),
                          "changes": ["이번 주 새로 본 후보"]})
            continue
        bits = []
        if old.get("feasibility") != row.get("feasibility"):
            bits.append(f"판단 {old.get('feasibility')} → {row.get('feasibility')}")
        old_u, new_u = set(old.get("unknowns") or []), set(row.get("unknowns") or [])
        if old_u - new_u:
            bits.append("빠진 미확인: " + " · ".join(sorted(old_u - new_u)[:2]))
        if new_u - old_u:
            bits.append("추가된 미확인: " + " · ".join(sorted(new_u - old_u)[:2]))
        old_lines, new_lines = old.get("lines") or {}, row.get("lines") or {}
        if old_lines.get("cash") != new_lines.get("cash"):
            bits.append("현금 문장이 바뀌었습니다")
        if old_lines.get("price") != new_lines.get("price"):
            bits.append("가격 문장이 바뀌었습니다")
        if bits:
            items.append({"name": row.get("name"), "region": row.get("region"), "changes": bits})
        else:
            unchanged += 1
    reason = "기록한 후보의 보류 이유는 지난주와 같습니다" if not items else None
    return {"ready": True, "week": current, "prev_week": prev, "items": items,
            "unchanged": unchanged, "reason": reason}


def shift_week(day: date, days: int) -> str:
    return week_key(day + timedelta(days=days))
