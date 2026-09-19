"""Durable per-job due times and renewable SQLite leases (one shared volume)."""
import threading
import time
from uuid import uuid4

from realty_signal import db


def _schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS jobs(name TEXT PRIMARY KEY, owner TEXT,
        lease_until REAL DEFAULT 0, due_at REAL DEFAULT 0, last_success REAL,
        last_attempt REAL, failures INTEGER DEFAULT 0, error TEXT)""")


def _claim(name, owner, lease_seconds):
    c = db.conn()
    try:
        _schema(c)
        c.execute("INSERT OR IGNORE INTO jobs(name) VALUES(?)", (name,))
        c.commit()
        c.execute("BEGIN IMMEDIATE")
        now = time.time()
        changed = c.execute("UPDATE jobs SET owner=?,lease_until=?,last_attempt=? WHERE name=? AND due_at<=? AND lease_until<=?",
                            (owner, now+lease_seconds, now, name, now, now)).rowcount
        c.commit()
        return bool(changed)
    finally:
        c.close()


def run(name, fn, *, interval, retry=3600, lease_seconds=300):
    owner = uuid4().hex
    if not _claim(name, owner, lease_seconds):
        return {"status": "not_due_or_running"}
    stop = threading.Event()
    def renew():
        while not stop.wait(lease_seconds / 3):
            c = db.conn()
            try:
                c.execute("UPDATE jobs SET lease_until=? WHERE name=? AND owner=?", (time.time()+lease_seconds, name, owner))
                c.commit()
            finally:
                c.close()
    heartbeat = threading.Thread(target=renew, daemon=True)
    heartbeat.start()
    status, error = "complete", None
    try:
        result = fn()
        if isinstance(result, dict) and result.get("ok") is False:
            raise RuntimeError("job_returned_failure")
        return {"status": status}
    except Exception as exc:
        # Error type only: source exception strings may contain credentials.
        status, error = "failed", type(exc).__name__
        return {"status": status, "error": error}
    finally:
        stop.set()
        heartbeat.join(timeout=1)
        c = db.conn()
        try:
            c.execute("""UPDATE jobs SET lease_until=0, owner=NULL, due_at=?,
                last_success=CASE WHEN ?='complete' THEN ? ELSE last_success END,
                failures=CASE WHEN ?='complete' THEN 0 ELSE failures+1 END, error=?
                WHERE name=? AND owner=?""",
                (time.time()+(interval if status == "complete" else retry), status, time.time(), status, error, name, owner))
            c.commit()
        finally:
            c.close()


def status():
    c = db.conn()
    try:
        _schema(c)
        return [dict(zip(("name", "due_at", "lease_until", "last_success", "last_attempt", "failures", "error"), row))
                for row in c.execute("SELECT name,due_at,lease_until,last_success,last_attempt,failures,error FROM jobs")]
    finally:
        c.close()
