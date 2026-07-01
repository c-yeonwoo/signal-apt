"""app.db 자동 백업 → S3 호환 오브젝트 스토리지(Cloudflare R2 / AWS S3 / Supabase Storage).

env 미설정 시 no-op(폴백). SQLite 온라인 백업(.backup)으로 실행 중에도 안전하게 스냅샷.

필요 env:
  BACKUP_S3_BUCKET, BACKUP_S3_KEY_ID, BACKUP_S3_SECRET
  BACKUP_S3_ENDPOINT (R2/Supabase 등 S3 호환 엔드포인트, AWS면 생략), BACKUP_S3_REGION(기본 auto)
"""

from __future__ import annotations

import gzip
import logging
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from realty_signal import db as _db

log = logging.getLogger("realty_signal")


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
