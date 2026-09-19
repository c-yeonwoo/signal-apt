"""Shared, paginated MOLIT evidence cache. Failure is never an empty market.

All consumers share (endpoint, district, month, schema), not apartment name.
Recent months are revisited for delayed filings/cancellations. No keys in logs.
"""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

from realty_signal import db, jsonx

_LOCKS = [threading.Lock() for _ in range(64)]
_NETWORK = threading.BoundedSemaphore(4)
SCHEMA_VERSION = "molit-v2"


def cancelled(item):
    return (item.findtext("cdealType") or "").strip().upper() in {"O", "Y", "1"} or bool((item.findtext("cdealDay") or "").strip())


def quota_blocked(key):
    return bool(db.kv_get(_quota_key(key), max_age=86400))


def _quota_key(key):
    day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    return f"molit:blocked:{day}:{sha256(key.encode()).hexdigest()[:16]}"


def _claim_request(key):
    if quota_blocked(key):
        return False
    counter = _quota_key(key).replace(":blocked:", ":calls:")
    c = db.conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT v FROM kv WHERE k=?", (counter,)).fetchone()
        used = int(jsonx.loads(row[0])) if row else 0
        if used >= int(os.getenv("MOLIT_DAILY_REQUEST_LIMIT", "9500")):
            c.execute("INSERT OR REPLACE INTO kv VALUES(?,?,?)", (_quota_key(key), "true", int(time.time())))
            c.commit()
            return False
        c.execute("INSERT OR REPLACE INTO kv VALUES(?,?,?)", (counter, str(used+1), int(time.time())))
        c.commit()
        return True
    finally:
        c.close()


def fetch_items(base, lawd, key, ym, *, timeout=12):
    if not (str(lawd).isdigit() and len(str(lawd)) == 5 and str(ym).isdigit() and len(str(ym)) == 6):
        return {"status": "failed", "items": [], "error": "invalid_identity"}
    identity = sha256(f"{SCHEMA_VERSION}:{base}:{lawd}:{ym}".encode()).hexdigest()
    ckey = f"molit:raw:{identity}"
    with _LOCKS[int(identity[:8], 16) % len(_LOCKS)]:
        cached = db.kv_get(ckey)
        if cached and cached["valid_until"] > time.time():
            return {**cached, "items": [ET.fromstring(x) for x in cached["items"]], "cached": True}
        failure = db.kv_get(ckey+":error", max_age=30)
        if failure:
            return {"status": "failed", "items": [], "error": failure}
        items, total, pages = [], None, 0
        deadline = time.monotonic() + 90
        error = None
        while pages < 100 and time.monotonic() < deadline:
            if not _claim_request(key):
                error = "quota_exhausted"
                break
            pages += 1
            url = f"{base}?serviceKey={key}&LAWD_CD={lawd}&DEAL_YMD={ym}&numOfRows=999&pageNo={pages}"
            try:
                with _NETWORK:
                    response = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "realty-signal/1.0"}), timeout=timeout)
                    try:
                        root = ET.fromstring(response.read())
                    finally:
                        if hasattr(response, "close"):
                            response.close()
                code = (root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "").strip()
                if code not in {"00", "000"}:
                    error = "quota_exhausted" if code.lstrip("0") == "22" else "upstream_error"
                    if error == "quota_exhausted":
                        db.kv_set(_quota_key(key), True)
                    break
                total = int(root.findtext(".//totalCount"))
                batch = list(root.iter("item"))
                items.extend(batch)
                if len(items) == total:
                    break
                if not batch or len(items) > total:
                    error = "incomplete_pages"
                    break
            except Exception:
                error = "transport_or_schema_error"
                break
        if error or total is None or len(items) != total:
            error = error or "incomplete_pages"
            db.kv_set(ckey+":error", error)
            # Last known good data is not silently promoted to fresh results.
            return {"status": "failed", "items": [], "error": error, "cached_available": bool(cached)}
        now = time.time()
        month_start = datetime.strptime(ym, "%Y%m").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc)-month_start).days
        active = [x for x in items if not cancelled(x)]
        result = {"status": "ok", "api_total": total, "pages": pages,
                  "cancelled": len(items)-len(active), "items": [ET.tostring(x, encoding="unicode") for x in active],
                  "fetched_at": now, "valid_until": now + (6*3600 if age_days < 100 else 30*86400),
                  "observed_month": ym, "published_at": None, "schema_version": SCHEMA_VERSION}
        db.kv_set(ckey, result)
        # Immutable revisions permit later audits without retaining credentials.
        content_hash = sha256(json.dumps(result["items"], sort_keys=True).encode()).hexdigest()
        c = db.conn()
        try:
            c.execute("CREATE TABLE IF NOT EXISTS source_revisions(identity TEXT, hash TEXT, fetched_at REAL, payload TEXT, PRIMARY KEY(identity,hash))")
            c.execute("INSERT OR IGNORE INTO source_revisions VALUES(?,?,?,?)", (identity, content_hash, now, json.dumps(result, ensure_ascii=False)))
            c.commit()
        finally:
            c.close()
        return {**result, "items": active, "cached": False}
