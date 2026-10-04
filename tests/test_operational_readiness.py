from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient

from realty_signal import api, auth, backup, jobs, store
from realty_signal.ingest import pipeline
from realty_signal.routes import market
from realty_signal.services import market_data as md


def test_scheduled_backup_never_records_missing_upload_as_success(monkeypatch):
    monkeypatch.setattr(backup, "enabled", lambda: False)
    disabled = jobs.run("backup_disabled", api._scheduled_backup, interval=86400)
    assert disabled == {"status": "failed", "error": "BackupNotConfigured"}
    assert next(row for row in jobs.status() if row["name"] == "backup_disabled")["last_success"] is None

    monkeypatch.setattr(backup, "enabled", lambda: True)
    monkeypatch.setattr(backup, "run_backup", lambda: None)
    failed = jobs.run("backup_upload_failed", api._scheduled_backup, interval=86400)
    assert failed == {"status": "failed", "error": "BackupUploadFailed"}
    assert next(row for row in jobs.status() if row["name"] == "backup_upload_failed")["last_success"] is None

    monkeypatch.setattr(backup, "run_backup", lambda: "signalapt/app-20261004-120000.db.gz")
    assert jobs.run("backup_uploaded", api._scheduled_backup, interval=86400) == {"status": "complete"}
    assert next(row for row in jobs.status() if row["name"] == "backup_uploaded")["last_success"] is not None


def test_ready_distinguishes_app_availability_from_kb_signal_age(monkeypatch, tmp_path):
    today = date(2026, 10, 5)
    monkeypatch.setattr(market, "today_kst", lambda: today)
    cache = tmp_path / "kb.parquet"
    cache.touch()
    monkeypatch.setattr(store, "CACHE_FILE", cache)
    monkeypatch.setattr(md, "data_age_days", lambda: 9.0)
    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(last_date=pd.Timestamp(today - timedelta(days=9))))
    response = TestClient(api.app).get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True, "kb_source_fresh_for_signal": False}

    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(last_date=pd.Timestamp(today - timedelta(days=8))))
    assert TestClient(api.app).get("/ready").json()["kb_source_fresh_for_signal"] is True


def test_operations_backup_status_is_admin_only(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "operator@example.com")
    monkeypatch.setattr(backup, "enabled", lambda: False)
    monkeypatch.setattr(pipeline, "cache_health", lambda **kwargs: {})
    from realty_signal import llm
    monkeypatch.setattr(llm, "usage_summary", lambda: {})
    client = TestClient(api.app)
    assert client.get("/api/operations").status_code in (401, 403)
    token, error = auth.signup("operator@example.com", "secret1", accept_tos=True)
    assert error is None
    client.cookies.set(auth.COOKIE, token)
    response = client.get("/api/operations")
    assert response.status_code == 200
    assert response.json()["backup"] == {"configured": False, "upload_job": None}
    assert response.json()["report_explanations"] == {"window_days": 7, "jobs": 0,
                                                        "by_status": {}, "by_failure_code": {}}


def test_public_pipeline_health_hides_personal_quicksale_cache(monkeypatch):
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "listing-owner@example.com")
    calls = []
    def health(*, include_private):
        calls.append(include_private)
        sources = {"kb_long": {"status": "ok"}}
        if include_private:
            sources["quicksale"] = {"status": "ok", "path": "/private/quicksale.json"}
        return {"sources": sources}
    monkeypatch.setattr(pipeline, "cache_health", health)

    guest_token, error = auth.signup("guest@example.com", "secret1", accept_tos=True)
    assert error is None
    guest = TestClient(api.app)
    guest.cookies.set(auth.COOKIE, guest_token)
    public = guest.get("/api/pipeline/health")
    assert public.status_code == 200
    assert "quicksale" not in public.json()["sources"]

    token, error = auth.signup("listing-owner@example.com", "secret1", accept_tos=True)
    assert error is None
    owner = TestClient(api.app)
    owner.cookies.set(auth.COOKIE, token)
    private = owner.get("/api/pipeline/health")
    assert private.status_code == 200
    assert private.json()["sources"]["quicksale"]["path"] == "/private/quicksale.json"
    assert calls == [False, True]
