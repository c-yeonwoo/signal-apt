"""Backup verification is a non-destructive restore drill, not a production restore."""

import gzip
import sqlite3
from io import BytesIO
from types import SimpleNamespace

import pytest

from realty_signal import backup


def _database(path):
    with sqlite3.connect(path) as connection:
        for table in ("users", "profile", "favorites", "kv"):
            connection.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO users(id) VALUES(1)")


def test_online_snapshot_restores_in_temp_without_modifying_live_db(tmp_path, monkeypatch):
    live = tmp_path / "app.db"
    _database(live)
    before = live.read_bytes()
    monkeypatch.setattr(backup._db, "DB", live)
    result = backup.verify_gz(backup.dump_gz())
    assert result["integrity"] == "ok"
    assert result["row_counts"] == {"users": 1, "profile": 0, "favorites": 0, "kv": 0}
    assert result["restored_bytes"] > 0
    assert live.read_bytes() == before


def test_restore_drill_rejects_corrupt_archive_and_incomplete_schema(tmp_path):
    with pytest.raises((gzip.BadGzipFile, EOFError, OSError)):
        backup.verify_gz(b"not a gzip archive")
    missing = tmp_path / "missing.db"
    with sqlite3.connect(missing) as connection:
        connection.execute("CREATE TABLE users(id INTEGER)")
    with pytest.raises(ValueError, match="backup_schema_incomplete"):
        backup.verify_gz(gzip.compress(missing.read_bytes()))


def test_remote_verification_reads_only_named_backup(tmp_path, monkeypatch):
    live = tmp_path / "app.db"
    _database(live)
    blob = gzip.compress(live.read_bytes())
    requests = []
    def client(service, **kwargs):
        assert service == "s3"
        return SimpleNamespace(get_object=lambda **args: (
            requests.append(args) or {"Body": BytesIO(blob)}))
    monkeypatch.setattr(backup, "enabled", lambda: True)
    monkeypatch.setattr(backup, "_cfg", lambda: {"endpoint": None, "bucket": "test-bucket",
                                                 "key": "test", "secret": "test", "region": "auto"})
    monkeypatch.setitem(__import__("sys").modules, "boto3", SimpleNamespace(client=client))
    with pytest.raises(ValueError, match="invalid_backup_key"):
        backup.verify_remote("other/private.db.gz")
    result = backup.verify_remote("signalapt/app-20261003-120000.db.gz")
    assert requests == [{"Bucket": "test-bucket", "Key": "signalapt/app-20261003-120000.db.gz"}]
    assert result["row_counts"]["users"] == 1


def test_scheduled_upload_is_read_back_before_retention(tmp_path, monkeypatch):
    live = tmp_path / "app.db"
    _database(live)
    monkeypatch.setattr(backup._db, "DB", live)
    monkeypatch.setattr(backup, "_cfg", lambda: {"endpoint": None, "bucket": "test-bucket",
                                                 "key": "test", "secret": "test", "region": "auto"})
    events = []
    uploaded = {}

    def put_object(**args):
        events.append("put")
        uploaded[args["Key"]] = args["Body"]

    def get_object(**args):
        events.append("get")
        return {"Body": BytesIO(uploaded[args["Key"]])}

    client = SimpleNamespace(
        put_object=put_object,
        get_object=get_object,
        list_objects_v2=lambda **_args: (events.append("list") or {"Contents": []}),
        delete_object=lambda **_args: events.append("delete"),
    )
    monkeypatch.setitem(__import__("sys").modules, "boto3",
                        SimpleNamespace(client=lambda *_args, **_kwargs: client))
    key = backup.run_backup()
    assert key in uploaded
    assert events == ["put", "get", "list"]


def test_unrestorable_or_different_remote_backup_keeps_older_objects(tmp_path, monkeypatch):
    live = tmp_path / "app.db"
    other = tmp_path / "other.db"
    _database(live)
    _database(other)
    with sqlite3.connect(other) as connection:
        connection.execute("INSERT INTO users(id) VALUES(2)")
    monkeypatch.setattr(backup._db, "DB", live)
    monkeypatch.setattr(backup, "_cfg", lambda: {"endpoint": None, "bucket": "test-bucket",
                                                 "key": "test", "secret": "test", "region": "auto"})

    for remote_blob in (b"not a gzip archive", gzip.compress(other.read_bytes())):
        events = []

        def put_object(**_args):
            events.append("put")

        def get_object(**_args):
            events.append("get")
            return {"Body": BytesIO(remote_blob)}

        client = SimpleNamespace(
            put_object=put_object,
            get_object=get_object,
            list_objects_v2=lambda **_args: (events.append("list") or {"Contents": []}),
            delete_object=lambda **_args: events.append("delete"),
        )
        monkeypatch.setitem(__import__("sys").modules, "boto3",
                            SimpleNamespace(client=lambda *_args, **_kwargs: client))
        assert backup.run_backup() is None
        assert events == ["put", "get"]
