from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from realty_signal import advisor, db, llm
from realty_signal.ingest import transaction_source as src
from realty_signal.ingest.complex import _items, SourceUnavailable
from realty_signal.storage import atomic_json


def test_quota_is_atomic_under_concurrent_requests():
    db.conn().close()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: db.usage_reserve(123, "nick", 3), range(24)))
    assert sum(ok for ok, _ in results) == 3
    assert db.usage_get(123, "nick") == 3


def test_llm_usage_and_cache_cost_without_storing_prompt():
    class Backend:
        def create(self, **kwargs):
            assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
            return SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                cache_read_input_tokens=1000, cache_creation_input_tokens=200))
    messages = llm.MeteredMessages(Backend(), feature="test", uid=42)
    messages.create(model="claude-sonnet-4-6", max_tokens=100,
                    system="private-personal-data", messages=[])
    row = llm.usage_summary()["rows"][0]
    assert row["actual_usage_usd"] == pytest.approx(.00165)
    assert row["unsettled_reserve_usd"] == 0
    assert "private-personal-data" not in str(row)


def test_daily_llm_budget_blocks_before_network(monkeypatch):
    monkeypatch.setenv("LLM_DAILY_BUDGET_USD", "0")
    with pytest.raises(llm.BudgetExceeded):
        llm.MeteredMessages(None, feature="test").create(model="claude-sonnet-4-6", max_tokens=100)


def test_failed_stream_retains_reservation():
    class Backend:
        @contextmanager
        def stream(self, **kwargs):
            yield object()
    messages = llm.MeteredMessages(Backend(), feature="stream")
    with pytest.raises(GeneratorExit):
        with messages.stream(model="claude-sonnet-4-6", max_tokens=100):
            raise GeneratorExit()
    row = llm.usage_summary()["rows"][0]
    assert row["unsettled_reserve_usd"] > 0


def test_tool_truncation_preserves_json_and_flags_partial():
    out = json.loads(advisor.tool_payload({"large": "가"*10000}))
    assert out["truncated"] is True
    with pytest.raises(ValueError):
        advisor._to_blocks([{"role": "user", "text": ["not text"]}])


def _response(xml):
    return SimpleNamespace(read=lambda: xml.encode())


def test_pagination_shared_cache_and_cancellation(monkeypatch):
    calls = []
    def fetch(req, **kwargs):
        page = parse_qs(urlparse(req.full_url).query)["pageNo"][0]
        calls.append(page)
        item = '<item><aptNm>A</aptNm><cdealType>Y</cdealType></item>' if page == "1" else '<item><aptNm>B</aptNm></item>'
        return _response(f'<response><resultCode>000</resultCode><totalCount>2</totalCount>{item}</response>')
    monkeypatch.setattr(src.urllib.request, "urlopen", fetch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: src.fetch_items("https://source", "11680", "key", "202608"), range(4)))
    assert calls == ["1", "2"]
    assert all(r["status"] == "ok" and len(r["items"]) == 1 for r in results)
    assert results[0]["cancelled"] == 1


def test_daily_quota_error_trips_circuit_across_months(monkeypatch):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        return _response('<response><resultCode>22</resultCode></response>')
    monkeypatch.setattr(src.urllib.request, "urlopen", fetch)
    for ym in ("202601", "202602", "202603"):
        assert src.fetch_items("https://source", "11680", "key", ym)["status"] == "failed"
    assert len(calls) == 1


def test_failure_is_not_zero_trade_month(monkeypatch):
    monkeypatch.setattr(src.urllib.request, "urlopen", lambda *a, **k: _response('<response><resultCode>99</resultCode></response>'))
    with pytest.raises(SourceUnavailable):
        _items("https://source", "11680", "key", "202608")


def test_empty_success_and_incomplete_response_are_distinct(monkeypatch):
    monkeypatch.setattr(src.urllib.request, "urlopen", lambda *a, **k: _response('<response><resultCode>00</resultCode><totalCount>0</totalCount></response>'))
    assert src.fetch_items("https://source", "11680", "key", "202601")["status"] == "ok"
    monkeypatch.setattr(src.urllib.request, "urlopen", lambda *a, **k: _response('<response><resultCode>00</resultCode><totalCount>3</totalCount></response>'))
    assert src.fetch_items("https://source", "11680", "key", "202602")["status"] == "failed"


def test_failed_atomic_publish_preserves_previous_file(tmp_path):
    path = tmp_path / "cache.json"
    atomic_json(path, {"old": True})
    with pytest.raises(ValueError):
        atomic_json(path, {"bad": float("nan")})
    assert json.loads(path.read_text()) == {"old": True}
