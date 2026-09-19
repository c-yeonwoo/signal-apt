"""Single paid-call boundary: atomic reservations, bounded input, usage-only ledger.

Prices: https://platform.claude.com/docs/en/about-claude/pricing (2026-09-19).
Estimated USD excludes tax/discounts; provider billing remains authoritative.
Reservations on failed/cancelled calls are retained until the UTC day ends.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4

from realty_signal import db

PRICING_VERSION = "anthropic-global-2026-09-19"
PRICES = {"claude-haiku-4-5": (1, 5), "claude-sonnet-4-6": (3, 15),
          "claude-opus-4-8": (5, 25)}
MAX_INPUT_BYTES = 100_000
MAX_OUTPUT_TOKENS = 2000
MAX_CALLS_PER_REQUEST = 6
REQUEST_SECONDS = 120


class BudgetExceeded(RuntimeError):
    pass


def _schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS llm_calls(
        id TEXT PRIMARY KEY, uid INTEGER, feature TEXT, model TEXT, day TEXT,
        started REAL, latency_ms INTEGER, status TEXT, reserved_usd REAL,
        cost_usd REAL, input_tokens INTEGER, output_tokens INTEGER,
        cache_read_tokens INTEGER, cache_write_tokens INTEGER, pricing_version TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_llm_day ON llm_calls(day)")


def _json_default(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    raise TypeError("Unsupported model payload")


def _reserve(uid, feature, kwargs):
    model = kwargs.get("model")
    if model not in PRICES:
        raise BudgetExceeded("unpriced_model")
    size = len(json.dumps(kwargs, ensure_ascii=False, default=_json_default).encode())
    output = kwargs.get("max_tokens", 0)
    if size > MAX_INPUT_BYTES or not 0 < output <= MAX_OUTPUT_TOKENS:
        raise BudgetExceeded("request_size_limit")
    # Conservative byte-based estimate, with cache-write and protocol allowance.
    inp_rate, out_rate = PRICES[model]
    reserved = ((size * 2 + 4096) * inp_rate * 1.25 + output * out_rate) / 1_000_000
    day = datetime.now(timezone.utc).date().isoformat()
    request_id = uuid4().hex
    c = db.conn()
    try:
        _schema(c)
        c.execute("BEGIN IMMEDIATE")
        used = c.execute("SELECT COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0) FROM llm_calls WHERE day=?", (day,)).fetchone()[0]
        active = c.execute("SELECT COUNT(*) FROM llm_calls WHERE status='reserved' AND started>?", (time.time() - REQUEST_SECONDS - 60,)).fetchone()[0]
        if used + reserved > float(os.getenv("LLM_DAILY_BUDGET_USD", "50")) or active >= int(os.getenv("LLM_MAX_CONCURRENT", "4")):
            raise BudgetExceeded("daily_budget_or_concurrency_limit")
        c.execute("INSERT INTO llm_calls(id,uid,feature,model,day,started,status,reserved_usd,pricing_version) VALUES(?,?,?,?,?,?,'reserved',?,?)",
                  (request_id, uid, feature, model, day, time.time(), reserved, PRICING_VERSION))
        c.commit()
    finally:
        c.close()
    return request_id


def _settle(request_id, response=None, status="failed"):
    c = db.conn()
    try:
        row = c.execute("SELECT model,started FROM llm_calls WHERE id=?", (request_id,)).fetchone()
        usage = getattr(response, "usage", None)
        counts = [int(getattr(usage, k, 0) or 0) for k in
                  ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
        ir, ore = PRICES[row[0]]
        cost = (counts[0]*ir + counts[1]*ore + counts[2]*ir*.1 + counts[3]*ir*1.25) / 1_000_000 if usage else None
        c.execute("UPDATE llm_calls SET status=?,cost_usd=?,input_tokens=?,output_tokens=?,cache_read_tokens=?,cache_write_tokens=?,latency_ms=? WHERE id=?",
                  (status, cost, *counts, round((time.time()-row[1])*1000), request_id))
        c.commit()
    finally:
        c.close()


class MeteredMessages:
    def __init__(self, backend, *, feature, uid=None):
        self.backend, self.feature, self.uid = backend, feature, uid
        self.started, self.calls = time.monotonic(), 0

    def _start(self, kwargs):
        if self.calls >= MAX_CALLS_PER_REQUEST or time.monotonic()-self.started >= REQUEST_SECONDS:
            raise BudgetExceeded("request_budget_limit")
        self.calls += 1
        if isinstance(kwargs.get("system"), str):
            kwargs["system"] = [{"type": "text", "text": kwargs["system"],
                                  "cache_control": {"type": "ephemeral"}}]
        kwargs["timeout"] = min(40, max(1, REQUEST_SECONDS - (time.monotonic()-self.started)))
        return _reserve(self.uid, self.feature, kwargs)

    def create(self, **kwargs):
        rid = self._start(kwargs)
        response = None
        try:
            response = self.backend.create(**kwargs)
            return response
        finally:
            _settle(rid, response, "complete" if response else "failed")

    @contextmanager
    def stream(self, **kwargs):
        rid = self._start(kwargs)
        response = None
        try:
            with self.backend.stream(**kwargs) as stream:
                yield stream
                response = stream.get_final_message()
        finally:
            _settle(rid, response, "complete" if response else "cancelled_or_failed")


def client(feature: str, uid: int | None = None):
    import anthropic
    from types import SimpleNamespace
    sdk = anthropic.Anthropic(timeout=40, max_retries=0)
    return SimpleNamespace(messages=MeteredMessages(sdk.messages, feature=feature, uid=uid))


def usage_summary():
    c = db.conn()
    try:
        _schema(c)
        rows = c.execute("""SELECT day,feature,model,COUNT(*),SUM(COALESCE(cost_usd,0)),
            SUM(CASE WHEN cost_usd IS NULL THEN reserved_usd ELSE 0 END)
            FROM llm_calls GROUP BY day,feature,model ORDER BY day DESC LIMIT 100""").fetchall()
        return {"pricing_version": PRICING_VERSION, "estimated": True,
                "rows": [dict(zip(("day", "feature", "model", "calls", "actual_usage_usd", "unsettled_reserve_usd"), row)) for row in rows]}
    finally:
        c.close()
