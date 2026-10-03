"""Personal report copies are explicit, immutable and permission-scoped."""

import json

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from realty_signal import db
from realty_signal.routes import reports_v2
from realty_signal.services import report_snapshots_v2 as snapshots


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "snapshots.db")
    db._migrated[0] = False


def _report(report_id="a" * 64):
    return {"schema_version": "report-v2-1", "type": "listing", "report_id": report_id,
            "subject": {"key": "일반매물:one", "kind": "일반매물", "name": "테스트 단지"},
            "asof": "2026-10-03", "price": {"이유": "원본 가격 근거"}}


def test_save_is_immutable_idempotent_and_owner_scoped(isolated_db):
    original = _report()
    first = snapshots.save(7, original)
    revised = _report()
    revised["price"]["이유"] = "변경된 가격 근거"
    again = snapshots.save(7, revised)
    assert again == first
    assert snapshots.get(7, original["report_id"], private_allowed=True)["report"] == original
    assert snapshots.get(8, original["report_id"], private_allowed=True) is None
    assert snapshots.get(7, original["report_id"], private_allowed=False) is None
    assert snapshots.list_for(7, private_allowed=False) == []
    assert snapshots.list_for(7, private_allowed=True)[0]["name"] == "테스트 단지"


def test_save_route_rechecks_current_report_and_permission(isolated_db, monkeypatch):
    report = _report()
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    allowed = {"value": True}
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: allowed["value"])
    monkeypatch.setattr(reports_v2, "listing_report", lambda request, key: JSONResponse(report))

    with pytest.raises(HTTPException) as changed:
        reports_v2.report_snapshot_save(None, {"key": "일반매물:one", "report_id": "b" * 64})
    assert changed.value.status_code == 409
    saved = json.loads(reports_v2.report_snapshot_save(None, {
        "key": "일반매물:one", "report_id": report["report_id"]}).body)
    assert saved["report_id"] == report["report_id"]
    assert json.loads(reports_v2.report_snapshot_get(None, report["report_id"]).body)["report"] == report
    assert len(json.loads(reports_v2.report_snapshots_list(None).body)["items"]) == 1

    monkeypatch.setattr(reports_v2, "listing_report", lambda request, key: (_ for _ in ()).throw(
        AssertionError("historical read must not refresh a vanished listing")))
    assert json.loads(reports_v2.report_snapshot_get(None, report["report_id"]).body)["report"] == report
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 8)
    with pytest.raises(HTTPException) as other_user:
        reports_v2.report_snapshot_get(None, report["report_id"])
    assert other_user.value.status_code == 404
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)

    allowed["value"] = False
    with pytest.raises(HTTPException) as denied:
        reports_v2.report_snapshot_save(None, {"key": "일반매물:one", "report_id": report["report_id"]})
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as hidden:
        reports_v2.report_snapshot_get(None, report["report_id"])
    assert hidden.value.status_code == 404
    assert json.loads(reports_v2.report_snapshots_list(None).body)["items"] == []


def test_snapshot_routes_require_login_without_reading_data(isolated_db, monkeypatch):
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: None)
    monkeypatch.setattr(reports_v2, "listing_report", lambda *_args: (_ for _ in ()).throw(
        AssertionError("report must not be read")))
    for action in (
        lambda: reports_v2.report_snapshot_save(None, {"key": "일반매물:one", "report_id": "a" * 64}),
        lambda: reports_v2.report_snapshots_list(None),
        lambda: reports_v2.report_snapshot_get(None, "a" * 64),
    ):
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 401
