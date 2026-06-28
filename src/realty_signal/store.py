"""파싱 결과 캐시 — 대용량 엑셀을 매번 파싱하지 않도록 parquet 로 보관.

build(xlsx) → data/cache/long.parquet  (tidy long)
load()      → KBWeekly  (캐시에서 즉시 로드)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from realty_signal.ingest import kb_weekly
from realty_signal.ingest.kb_weekly import KBWeekly

CACHE_DIR = Path("data/cache")
CACHE_FILE = CACHE_DIR / "long.parquet"


def build(xlsx_path: str | Path, out: Path = CACHE_FILE) -> KBWeekly:
    """엑셀을 파싱해 parquet 캐시로 저장하고 KBWeekly 반환."""
    kb = kb_weekly.load(xlsx_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    kb.long.to_parquet(out, index=False)
    return kb


def load(cache: Path = CACHE_FILE) -> KBWeekly:
    """캐시(parquet)에서 KBWeekly 로드. 없으면 안내."""
    if not cache.exists():
        raise FileNotFoundError(
            f"캐시 없음: {cache}. 먼저 `signal build <xlsx>` 로 생성하세요."
        )
    return KBWeekly(long=pd.read_parquet(cache))
