"""Source-observed asking-price changes for the private sale listing feeds.

Only successful refreshes write observations. Raw supplier identifiers are hashed,
unchanged prices are not repeated, and history is retained for a bounded window.
This is a record of collected asking prices, never a record of completed sales.
"""

from __future__ import annotations

import hashlib
import json
import math
import time

from realty_signal import db
from realty_signal.services.listing_inventory import SALE_KINDS

RETENTION_SECONDS = 90 * 86400
_ID_FIELDS = {"baroezip": ("naver_id",), "hanbang": ("hanbang_id",)}


def _listing_hash(source: object, ref: object) -> str | None:
    fields = _ID_FIELDS.get(source)
    if not fields or not isinstance(ref, dict):
        return None
    value = next((ref.get(field) for field in fields
                  if type(ref.get(field)) in (str, int) and str(ref.get(field)).strip()), None)
    if value is None:
        return None
    canonical = json.dumps([source, str(value)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _positive_price(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and 0 < price < 1_000_000_000 else None


def record_source_rows(source: str, rows: list[dict], *, now: int | None = None) -> int:
    """Store changed asking prices from fresh rows returned by one successful crawl."""
    now = int(time.time()) if now is None else int(now)
    cutoff = now - RETENTION_SECONDS
    valid = []
    for row in rows:
        if not isinstance(row, dict) or row.get("stale") or row.get("degraded"):
            continue
        price = _positive_price(row.get("호가"))
        identity = _listing_hash(source, {"naver_id": row.get("naver_id"),
                                          "hanbang_id": row.get("hanbang_id")})
        observed = row.get("fetched_at")
        if (price is None or identity is None or isinstance(observed, bool)
                or not isinstance(observed, (int, float)) or not math.isfinite(observed)):
            continue
        observed = int(observed)
        if observed < cutoff or observed > now + 300:
            continue
        valid.append((identity, observed, price))
    if not valid:
        # Retention cleanup is intentionally tied to a successful refresh invocation.
        c = db.conn()
        try:
            c.execute("DELETE FROM listing_price_history_v1 WHERE observed_at<?", (cutoff,))
            c.commit()
        finally:
            c.close()
        return 0

    c = db.conn()
    written = 0
    try:
        c.execute("BEGIN IMMEDIATE")
        for identity, observed, price in valid:
            previous = c.execute("SELECT price_manwon FROM listing_price_history_v1 "
                                 "WHERE listing_hash=? ORDER BY observed_at DESC LIMIT 1",
                                 (identity,)).fetchone()
            if previous and float(previous[0]) == price:
                continue
            written += c.execute("INSERT OR IGNORE INTO listing_price_history_v1 "
                                 "(listing_hash,observed_at,price_manwon) VALUES(?,?,?)",
                                 (identity, observed, price)).rowcount
        c.execute("DELETE FROM listing_price_history_v1 WHERE observed_at<?", (cutoff,))
        c.commit()
        return written
    finally:
        c.close()


def _latest_two(hashes: list[str], *, since: int) -> dict[str, list[tuple[int, float]]]:
    if not hashes:
        return {}
    found: dict[str, list[tuple[int, float]]] = {}
    c = db.conn()
    try:
        for start in range(0, len(hashes), 400):
            chunk = hashes[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = c.execute(
                "SELECT listing_hash,observed_at,price_manwon FROM ("
                "SELECT listing_hash,observed_at,price_manwon,"
                "ROW_NUMBER() OVER(PARTITION BY listing_hash ORDER BY observed_at DESC) AS n "
                f"FROM listing_price_history_v1 WHERE observed_at>=? AND listing_hash IN ({placeholders})"
                ") WHERE n<=2 ORDER BY listing_hash,n", (since, *chunk)).fetchall()
            for identity, observed, price in rows:
                found.setdefault(identity, []).append((int(observed), float(price)))
        return found
    finally:
        c.close()


def attach(rows: list[dict], *, now: int | None = None) -> list[dict]:
    """Add a price-change clue without mutating rows or writing from a read path."""
    now = int(time.time()) if now is None else int(now)
    prepared = []
    for row in rows:
        if (row.get("유형") not in SALE_KINDS or row.get("stale") or row.get("degraded")
                or row.get("source_conflict") or row.get("price_kind") != "asking"):
            continue
        identity = _listing_hash(row.get("source"), row.get("ref"))
        price = _positive_price(row.get("총액"))
        observed = row.get("fetched_at")
        if (identity and price is not None and isinstance(observed, (int, float))
                and not isinstance(observed, bool) and math.isfinite(observed)):
            prepared.append((identity, price, int(observed)))
    try:
        histories = _latest_two(list({item[0] for item in prepared}), since=now - RETENTION_SECONDS)
    except Exception:  # A history store outage must not hide listings or break current price evidence.
        histories = {}

    out = []
    for row in rows:
        evidence = None
        if row.get("유형") in SALE_KINDS and not row.get("stale") and not row.get("degraded"):
            identity = _listing_hash(row.get("source"), row.get("ref"))
            price = _positive_price(row.get("총액"))
            observed = row.get("fetched_at")
            history = histories.get(identity or "", [])
            if (price is not None and isinstance(observed, (int, float)) and not isinstance(observed, bool)
                    and len(history) == 2 and history[0][0] <= int(observed)
                    and history[0][1] == price and history[1][1] > price):
                old_at, old_price = history[1]
                new_at, new_price = history[0]
                evidence = {
                    "상태": "수집호가인하관측",
                    "이전호가": round(old_price), "현재호가": round(new_price),
                    "차액": round(new_price - old_price),
                    "차이율": round((new_price - old_price) / old_price * 100, 1),
                    "관측간일수": max(0, (new_at - old_at) // 86400),
                    "확인시각": new_at,
                    "관측횟수": 2,
                    "기준": "동일 원천 매물의 서로 다른 수집 시점 호가",
                    "안내": "수집 호가 변화입니다. 실제 거래가·판매 상태·인하 이유는 확인되지 않았습니다.",
                }
        out.append({**row, "price_reduction": evidence} if row.get("유형") in SALE_KINDS else row)
    return out
