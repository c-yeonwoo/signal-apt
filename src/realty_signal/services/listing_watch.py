"""저장한 매물의 현재 수집값과 탐색용 대안을 계산한다.

매물 키는 원천 ID를 포함하며 가격과 무관하다. 사라진 매물은 거래완료라고 추정하지 않는다.
"""

from __future__ import annotations


WATCHABLE = {"급매", "찐매물", "일반매물", "청약", "경매"}
PRIVATE = {"급매", "찐매물", "일반매물"}


def public_fields(row: dict) -> dict:
    ref = row.get("ref") or {}
    return {"key": row.get("key"), "kind": row.get("유형"), "name": row.get("단지명"),
            "region": row.get("지역"), "price": row.get("총액"), "pyeong": row.get("평형"),
            "dday": ref.get("Dday"), "status": row.get("지표값") if row.get("유형") == "청약" else None,
            "stale": bool(row.get("stale")), "fetched_at": row.get("fetched_at")}


def _same_complex(a: dict, b: dict) -> bool:
    if not a.get("단지명") or not a.get("지역"):
        return False
    ar, br = a.get("ref") or {}, b.get("ref") or {}
    if ar.get("complex_no") and br.get("complex_no"):
        return str(ar["complex_no"]) == str(br["complex_no"])
    if ar.get("hanbang_complex_id") and br.get("hanbang_complex_id"):
        return str(ar["hanbang_complex_id"]) == str(br["hanbang_complex_id"])
    return a["단지명"] == b.get("단지명") and a["지역"] == b.get("지역")


def _same_source_listing(a: dict, b: dict) -> bool:
    ar, br = a.get("ref") or {}, b.get("ref") or {}
    for key in ("naver_id", "hanbang_id"):
        if ar.get(key) and br.get(key) and str(ar[key]) == str(br[key]):
            return True
    return False


def _similar(a: dict, b: dict) -> bool:
    if a.get("유형") != b.get("유형") or a.get("지역") != b.get("지역"):
        return False
    ap, bp = a.get("총액"), b.get("총액")
    if bool(ap) != bool(bp):
        return False
    if ap and not 0.75 <= bp / ap <= 1.25:
        return False
    aa, ba = a.get("평형"), b.get("평형")
    if aa and ba and not 0.8 <= ba / aa <= 1.2:
        return False
    return True


def build(saved: list[dict], current: list[dict]) -> list[dict]:
    by_key = {row.get("key"): row for row in current if row.get("key")}
    out = []
    for watch in saved:
        row = by_key.get(watch["key"])
        item = {**watch, "current": public_fields(row) if row else None,
                "price_change": None, "alternatives": []}
        if not row:
            # 수집 범위·원천 오류·매물 종료를 구분할 증거가 없으므로 상태를 단정하지 않는다.
            out.append(item)
            continue
        old, now = watch.get("saved_price"), row.get("총액")
        if old is not None and now is not None and not row.get("stale"):
            item["price_change"] = now - old
        others = [candidate for candidate in current
                  if candidate.get("key") != watch["key"] and candidate.get("key")
                  and not _same_source_listing(row, candidate)]
        same = [candidate for candidate in others if _same_complex(row, candidate)]
        similar = [candidate for candidate in others
                   if not _same_complex(row, candidate) and _similar(row, candidate)]
        item["alternatives"] = [
            {**public_fields(candidate), "reason": "같은 단지의 다른 매물"} for candidate in same[:3]
        ] + [
            {**public_fields(candidate),
             "reason": "같은 지역·유형·비슷한 가격대" if row.get("총액") else "같은 지역·유형"}
            for candidate in similar[:max(0, 3 - len(same[:3]))]
        ]
        out.append(item)
    return out
