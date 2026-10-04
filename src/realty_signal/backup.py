"""app.db 자동 백업 → S3 호환 오브젝트 스토리지(Cloudflare R2 / AWS S3 / Supabase Storage).

env 미설정 시 no-op(폴백). SQLite 온라인 백업(.backup)으로 실행 중에도 안전하게 스냅샷.

필요 env:
  BACKUP_S3_BUCKET, BACKUP_S3_KEY_ID, BACKUP_S3_SECRET
  BACKUP_S3_ENDPOINT (R2/Supabase 등 S3 호환 엔드포인트, AWS면 생략), BACKUP_S3_REGION(기본 auto)
"""

from __future__ import annotations

import gzip
from hashlib import sha256
from io import BytesIO
import logging
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

from realty_signal import db as _db

log = logging.getLogger("realty_signal")
_REQUIRED_TABLES = ("users", "profile", "favorites", "kv")
_MAX_VERIFY_BYTES = 4 * 1024**3


class BackupNotConfigured(RuntimeError):
    """The scheduled upload cannot run without its storage credentials."""


class BackupUploadFailed(RuntimeError):
    """No remote object was confirmed for this scheduled attempt."""


def _cfg() -> dict:
    e = os.environ
    return {
        "endpoint": e.get("BACKUP_S3_ENDPOINT"),
        "bucket": e.get("BACKUP_S3_BUCKET"),
        "key": e.get("BACKUP_S3_KEY_ID"),
        "secret": e.get("BACKUP_S3_SECRET"),
        "region": e.get("BACKUP_S3_REGION", "auto"),
    }


def enabled() -> bool:
    c = _cfg()
    return bool(c["bucket"] and c["key"] and c["secret"])


def dump_gz() -> bytes:
    """실행 중 안전한 온라인 백업 → gzip 바이트."""
    src = sqlite3.connect(_db.DB)
    try:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            dst = sqlite3.connect(tmp.name)
            src.backup(dst)
            dst.close()
            return gzip.compress(Path(tmp.name).read_bytes())
    finally:
        src.close()


def verify_gz(data: bytes) -> dict:
    """Restore into an isolated temporary DB and check integrity without touching app.db."""
    with tempfile.TemporaryDirectory(prefix="signalapt-restore-check-") as directory:
        path = Path(directory) / "restored.db"
        size = 0
        with gzip.GzipFile(fileobj=BytesIO(data)) as source, path.open("wb") as restored:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_VERIFY_BYTES:
                    raise ValueError("backup_too_large")
                restored.write(chunk)
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                checks = [row[0] for row in connection.execute("PRAGMA integrity_check")]
                if checks != ["ok"]:
                    raise ValueError("backup_integrity_failed")
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if not set(_REQUIRED_TABLES).issubset(tables):
                    raise ValueError("backup_schema_incomplete")
                counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in _REQUIRED_TABLES}
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError("backup_not_sqlite") from exc
        return {"sha256": sha256(data).hexdigest(), "restored_bytes": size,
                "integrity": "ok", "row_counts": counts}


def verify_remote(key: str) -> dict:
    """Read and restore-check one named S3 backup; never replaces the live DB."""
    if not enabled():
        raise ValueError("backup_not_configured")
    if not re.fullmatch(r"signalapt/app-\d{8}-\d{6}\.db\.gz", key):
        raise ValueError("invalid_backup_key")
    import boto3
    c = _cfg()
    s3 = boto3.client("s3", endpoint_url=c["endpoint"] or None,
                      aws_access_key_id=c["key"], aws_secret_access_key=c["secret"],
                      region_name=c["region"])
    response = s3.get_object(Bucket=c["bucket"], Key=key)
    body = response["Body"]
    try:
        return verify_gz(body.read())
    finally:
        body.close()


def run_backup(keep: int = 14) -> str | None:
    """app.db 스냅샷을 S3 호환 스토리지에 업로드. 성공 시 오브젝트 키, 아니면 None."""
    if not enabled() or not _db.DB.exists():
        return None
    try:
        import boto3
    except ImportError:
        log.warning("boto3 미설치 — 백업 스킵")
        return None
    c = _cfg()
    try:
        s3 = boto3.client("s3", endpoint_url=c["endpoint"] or None,
                          aws_access_key_id=c["key"], aws_secret_access_key=c["secret"],
                          region_name=c["region"])
        data = dump_gz()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        key = f"signalapt/app-{stamp}.db.gz"
        s3.put_object(Bucket=c["bucket"], Key=key, Body=data)
        # A successful PUT does not prove that the object can be read or restored.
        # Keep older backups until the new object passes the same restore check
        # operators use for their read-only drill.
        verified = verify_remote(key)
        if verified["sha256"] != sha256(data).hexdigest():
            raise ValueError("backup_remote_checksum_mismatch")
        # 오래된 백업 정리 (최근 keep개만 유지)
        try:
            objs = s3.list_objects_v2(Bucket=c["bucket"], Prefix="signalapt/app-").get("Contents", [])
            for o in sorted(objs, key=lambda x: x["LastModified"])[:-keep]:
                s3.delete_object(Bucket=c["bucket"], Key=o["Key"])
        except Exception:  # noqa: BLE001 — 정리 실패는 백업 성공에 영향 없음
            pass
        log.warning("백업 완료: %s (%d bytes)", key, len(data))
        return key
    except Exception as e:  # noqa: BLE001
        log.error("백업 실패: %s", e)
        return None
