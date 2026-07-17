"""이벤트 기반 매물 랭킹 보조 (Phase 4) — ML 없이 클릭/상세 빈도 가중.

listing_detail_open · listing_click 의 region/kind 집계 → 타이밍점수에 소폭 가점.
개인화는 로그인 유저 events, 전역은 전체 유저 집계(콜드스타트).
"""

from __future__ import annotations

import json
import time
from collections import defaultdict

from realty_signal import db

_RANK_EVENTS = frozenset({"listing_detail_open", "listing_click", "timing_card_expand"})
MAX_BONUS = 8  # 타이밍점수 가점 상한


def _parse_props(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def engagement_scores(
    *,
    uid: int | None = None,
    days: int = 30,
) -> dict[str, dict[str, float]]:
    """{region: {kind: score}} — 개인 이벤트 우선, 없으면 전역."""
    since = int(time.time()) - max(1, days) * 86400
    c = db.conn()
    if uid:
        rows = c.execute(
            "SELECT name, props FROM events WHERE uid=? AND ts>=? AND name IN (?,?,?)",
            (uid, since, *_RANK_EVENTS),
        ).fetchall()
    else:
        rows = []
    if not rows:
        rows = c.execute(
            "SELECT name, props FROM events WHERE ts>=? AND name IN (?,?,?)",
            (since, *_RANK_EVENTS),
        ).fetchall()
    c.close()

    counts: dict[tuple[str, str], int] = defaultdict(int)
    for name, props_s in rows:
        p = _parse_props(props_s)
        region = (p.get("region") or "").strip()
        kind = (p.get("kind") or p.get("유형") or "").strip() or "*"
        if not region:
            continue
        w = 2 if name == "listing_detail_open" else 1
        counts[(region, kind)] += w
        counts[(region, "*")] += w

    if not counts:
        return {}
    mx = max(counts.values()) or 1
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for (region, kind), n in counts.items():
        out[region][kind] = round(n / mx, 3)
    return dict(out)


def apply_engagement_bonus(
    listings: list[dict],
    scores: dict[str, dict[str, float]],
    *,
    max_bonus: int = MAX_BONUS,
) -> list[dict]:
    """listings 복사본에 타이밍점수/기회도 가점 + engagement_bonus 필드."""
    if not scores or not listings:
        return listings
    out = []
    for x in listings:
        row = dict(x)
        region = row.get("지역") or ""
        kind = row.get("유형") or ""
        reg = scores.get(region) or {}
        frac = reg.get(kind)
        if frac is None:
            frac = reg.get("*")
        if frac:
            bonus = round(frac * max_bonus)
            base = row.get("타이밍점수")
            if base is None:
                base = row.get("기회도")
            if base is not None and bonus:
                new = min(100, int(base) + bonus)
                row["타이밍점수"] = new
                row["기회도"] = new
                row["engagement_bonus"] = bonus
                why = row.get("타이밍근거") or row.get("기회도근거") or ""
                extra = f"관심도(+{bonus})"
                row["타이밍근거"] = f"{why} · {extra}" if why else extra
                row["기회도근거"] = row["타이밍근거"]
        out.append(row)
    return out
