#!/usr/bin/env python3
"""Read-only warm-cache benchmark for the authenticated Signal APT 2.0 paths.

Credentials and listing identity are read only from environment variables and
are never printed. The script does not clear caches or request source refreshes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
import platform
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


COOKIE_ENV = "SIGNAL_APT_SESSION_COOKIE"
LISTING_KEY_ENV = "SIGNAL_APT_BENCH_LISTING_KEY"
BASE_URL_ENV = "SIGNAL_APT_BASE_URL"
SPEC_FILE_ENV = "SIGNAL_APT_BENCH_SPEC_FILE"
DISCOVERY_P95_TARGET_MS = 500
REPORT_P95_TARGET_MS = 800
MAX_RESPONSE_BYTES = 50 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    """Never forward an authenticated request or cookie through a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPEN = build_opener(_NoRedirect).open


class BenchmarkError(RuntimeError):
    pass


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise BenchmarkError("base_url_invalid")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise BenchmarkError("https_required")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BenchmarkError("base_url_must_not_contain_credentials_or_query")
    return value.rstrip("/")


def percentile(values: list[float], percentile_value: float) -> float:
    if not values or not 0 < percentile_value <= 100:
        raise ValueError("invalid_percentile_input")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value / 100 * len(ordered)) - 1)
    return round(ordered[index], 1)


def _request_json(url: str, cookie: str, payload: dict | None) -> tuple[dict, float]:
    headers = {"Accept": "application/json", "Cookie": cookie}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    started = time.perf_counter()
    req = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with _OPEN(req, timeout=30) as response:
            status = response.status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise BenchmarkError(f"http_status_{exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        # Do not include an exception string that could contain request details.
        raise BenchmarkError(f"transport_{type(exc).__name__}") from None
    elapsed_ms = (time.perf_counter() - started) * 1000
    if len(raw) > MAX_RESPONSE_BYTES:
        raise BenchmarkError("response_too_large")
    if status < 200 or status >= 300:
        raise BenchmarkError(f"http_status_{status}")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BenchmarkError("invalid_json_response") from None
    if not isinstance(result, dict):
        raise BenchmarkError("unexpected_response_shape")
    return result, elapsed_ms


def _sample(label: str, url: str, cookie: str, payload: dict | None,
            warmups: int, runs: int, *, require_personal: bool = False) -> dict:
    initial, initial_ms = _request_json(url, cookie, payload)
    if require_personal and initial.get("private_access") is not True:
        raise BenchmarkError("personal_listing_access_not_confirmed")
    for _ in range(warmups):
        _request_json(url, cookie, payload)
    times = [_request_json(url, cookie, payload)[1] for _ in range(runs)]
    return {"label": label, "initial_ms": round(initial_ms, 1),
            "steady_runs": runs, "warmups": warmups,
            "p50_ms": percentile(times, 50), "p95_ms": percentile(times, 95),
            "max_ms": round(max(times), 1), "status": "ok",
            "initial_source_summary": _source_summary(initial) if label == "discovery" else None}


def _source_summary(result: dict) -> dict:
    groups = result.get("groups")
    if not isinstance(groups, dict):
        groups = {}
    sources = []
    for source in result.get("sources") or []:
        if not isinstance(source, dict):
            continue
        sources.append({"kind": source.get("kind"), "state": source.get("state"),
                        "count": source.get("count"),
                        "region_count": len(source.get("regions") or [])
                        if isinstance(source.get("regions"), list) else None})
    return {"private_access": result.get("private_access"),
            "candidate_count": sum(len(group) for group in groups.values() if isinstance(group, list)),
            "sources": sources}


def _load_spec(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as spec_file:
            value = json.load(spec_file)
    except (OSError, json.JSONDecodeError):
        raise BenchmarkError("discovery_spec_unreadable") from None
    if not isinstance(value, dict):
        raise BenchmarkError("discovery_spec_must_be_object")
    return value


def run(base_url: str, cookie: str, listing_key: str, spec: dict,
        runs: int = 30, warmups: int = 3) -> dict:
    if runs < 30 or warmups < 0:
        raise BenchmarkError("runs_must_be_at_least_30_and_warmups_nonnegative")
    if not cookie or "\n" in cookie or "\r" in cookie:
        raise BenchmarkError("session_cookie_missing_or_invalid")
    if not listing_key or ":" not in listing_key or any(ch in listing_key for ch in "\r\n"):
        raise BenchmarkError("listing_key_missing_or_invalid")
    base_url = _base_url(base_url)
    discovery = _sample("discovery", f"{base_url}/api/v2/discovery", cookie, spec,
                        warmups, runs, require_personal=True)
    report_url = f"{base_url}/api/v2/listings/report?{urlencode({'key': listing_key})}"
    report = _sample("listing_report", report_url, cookie, None, warmups, runs)
    return {"schema_version": 1, "measured_at": datetime.now(timezone.utc).isoformat(),
            "host": urlsplit(base_url).netloc, "python": platform.python_version(),
            "method": "sequential_client_elapsed_ms; one initial request, then warmups and steady samples",
            "cache_policy": "read-only; no cache clearing or source refresh",
            "results": [discovery, report],
            "targets": {"discovery_p95_ms": DISCOVERY_P95_TARGET_MS,
                        "listing_report_p95_ms": REPORT_P95_TARGET_MS}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        result = run(os.environ.get(BASE_URL_ENV, "https://signal-apt.up.railway.app"),
                     os.environ.get(COOKIE_ENV, ""), os.environ.get(LISTING_KEY_ENV, ""),
                     _load_spec(os.environ.get(SPEC_FILE_ENV)), args.runs, args.warmups)
    except BenchmarkError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
