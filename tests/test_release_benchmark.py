import json
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "benchmark_release", Path(__file__).resolve().parents[1] / "scripts" / "benchmark_release.py")
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def test_percentile_uses_nearest_rank_and_rejects_empty_samples():
    assert benchmark.percentile([1, 2, 3, 4], 50) == 2
    assert benchmark.percentile([1, 2, 3, 4], 95) == 4
    with pytest.raises(ValueError):
        benchmark.percentile([], 95)


def test_benchmark_reports_only_aggregate_source_counts_and_never_credentials_or_listing_key(monkeypatch):
    calls = []
    def request(url, cookie, payload):
        calls.append((url, cookie, payload))
        if payload is not None:
            result = {"private_access": True,
                      "groups": {"matched": [{"key": "private:item"}, {"key": "private:item2"}]},
                      "sources": [{"kind": "일반매물", "state": "ready", "count": 2,
                                   "regions": ["노원구", "도봉구"]}]}
        else:
            result = {"subject": {"key": "private:item", "region": "노원구"}}
        return result, 25.0
    monkeypatch.setattr(benchmark, "_request_json", request)
    result = benchmark.run("https://example.test", "rsm_session=secret-cookie",
                           "급매:private-listing-id", {}, runs=30, warmups=1)
    serialized = json.dumps(result, ensure_ascii=False)
    assert len(calls) == 64  # discovery and report each have one initial, one warmup, and 30 samples
    assert result["results"][0]["p50_ms"] == 25
    assert result["results"][0]["initial_source_summary"] == {
        "private_access": True, "candidate_count": 2,
        "sources": [{"kind": "일반매물", "state": "ready", "count": 2, "region_count": 2}]}
    assert "secret-cookie" not in serialized
    assert "private-listing-id" not in serialized
    assert "노원구" not in serialized


@pytest.mark.parametrize("base_url,reason", [
    ("http://example.test", "https_required"),
    ("https://user:password@example.test", "base_url_must_not_contain_credentials_or_query"),
])
def test_external_benchmark_refuses_insecure_or_credential_bearing_base_url(base_url, reason):
    with pytest.raises(benchmark.BenchmarkError, match=reason):
        benchmark.run(base_url, "cookie", "급매:id", {})


def test_authenticated_requests_are_never_redirected_to_another_host():
    assert benchmark._NoRedirect().redirect_request(
        None, None, 302, "Found", {"Location": "https://other.example/"},
        "https://other.example/") is None


def test_benchmark_requires_personal_listing_access_before_running_samples(monkeypatch):
    calls = []
    monkeypatch.setattr(benchmark, "_request_json",
                        lambda *args: calls.append(args) or ({"private_access": False}, 10.0))
    with pytest.raises(benchmark.BenchmarkError, match="personal_listing_access_not_confirmed"):
        benchmark.run("https://example.test", "cookie", "급매:id", {}, runs=30, warmups=0)
    assert len(calls) == 1
