import json
from types import SimpleNamespace

import pandas as pd
from typer.testing import CliRunner

from realty_signal import cli, db, region_identity_audit as audit, store


def _kb(*, verified=True):
    return SimpleNamespace(
        identity_verified=verified,
        regions=["서울", "강남구", "노원구", "중구", "연수구"],
        codes={"서울": "1100000000", "강남구": "1168000000", "노원구": "1135000000",
               "중구": "1114000000", "연수구": "2818500000"},
        last_date=pd.Timestamp("2026-09-28"),
    )


def test_region_audit_classifies_without_auto_remapping():
    kb = _kb()
    assert audit.classify("kb:1168000000", kb) == "verified_code"
    assert audit.classify("노원구", kb) == "unique_legacy_name"
    assert audit.classify("중구", kb) == "needs_reselection"
    assert audit.classify("kb:2811000000", kb) == "needs_reselection"
    assert audit.classify("서울", kb, district_required=True) == "not_district"
    assert audit.classify("kb:1168000000", _kb(verified=False)) == "unverified"
    assert audit.classify("kb:123", kb) == "unverified"


def test_read_only_audit_counts_user_surfaces_without_pii():
    connection = db.conn()
    connection.execute("INSERT INTO favorites(uid,kind,key,label,ts) VALUES(1,'region','중구','old',1)")
    connection.execute("INSERT INTO favorites(uid,kind,key,label,ts) VALUES(1,'region','노원구','old',2)")
    connection.execute("INSERT INTO favorites(uid,kind,key,label,ts) VALUES(1,'complex','서울|test-complex','old',3)")
    connection.execute("INSERT INTO listing_watch(uid,key,kind,name,region,saved_price,created_at) "
                       "VALUES(1,'listing-secret','일반매물','test-complex','노원구',100000,1)")
    connection.execute("INSERT INTO region_watch_state_v2 VALUES(1,'중구','evidence',1)")
    connection.execute("INSERT INTO alert_outbox_v2 "
                       "(id,uid,subject_type,subject_key,kind,evidence_revision,payload,created_at) "
                       "VALUES('a',1,'region','kb:1168000000','change','r','{}',1)")
    connection.execute("INSERT INTO profile(uid,data) VALUES(1,?)", (json.dumps({
        "매수지역코드": "kb:1168000000", "매수지역": "노원구"}),))
    connection.commit()
    connection.close()

    before = db.DB.stat().st_mtime_ns
    report = audit.audit(db.DB, _kb())
    assert db.DB.stat().st_mtime_ns == before
    assert report["surfaces"]["region_favorites"] == {
        "needs_reselection": 1, "unique_legacy_name": 1}
    assert report["surfaces"]["complex_favorites"] == {"not_district": 1}
    assert report["surfaces"]["region_alerts"] == {"verified_code": 1}
    assert report["surfaces"]["buyer_profiles"] == {"conflict": 1}
    output = json.dumps(report, ensure_ascii=False)
    for private_value in ("중구", "노원구", "test-complex", "listing-secret"):
        assert private_value not in output


def test_audit_does_not_create_missing_database(tmp_path):
    path = tmp_path / "never-created.db"
    try:
        audit.audit(path, _kb())
        assert False, "missing database must be rejected"
    except FileNotFoundError:
        assert not path.exists()


def test_region_audit_cli_outputs_aggregate_json(monkeypatch):
    connection = db.conn()
    connection.close()
    monkeypatch.setattr(store, "load", lambda: _kb())
    result = CliRunner().invoke(cli.app, ["region-audit"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["kb_identity_verified"] is True
    assert "surfaces" in parsed
