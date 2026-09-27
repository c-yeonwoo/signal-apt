"""파싱 결과 캐시 — 대용량 엑셀을 매번 파싱하지 않도록 parquet 로 보관.

build(xlsx) → data/cache/long.parquet  (tidy long)
load()      → KBWeekly  (캐시에서 즉시 로드)
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from realty_signal import config
from realty_signal.ingest import kb_datahub, kb_supply, kb_weekly, locality, volume
from realty_signal.ingest.kb_weekly import KBWeekly

CACHE_DIR = Path("data/cache")
CACHE_FILE = CACHE_DIR / "long.parquet"
SUPPLY_FILE = CACHE_DIR / "supply.parquet"
CODES_FILE = CACHE_DIR / "codes.json"
LOCALITY_FILE = CACHE_DIR / "locality.parquet"
LOCALITY_MODEL_VERSION = "locality-complete-v2"
MACRO_FILE = CACHE_DIR / "macro.json"
VOLUME_FILE = CACHE_DIR / "volume.json"
V2_REAL_TRADE_DIR = CACHE_DIR / "v2_real_trade"


def _recent_months(n: int = 3) -> list[str]:
    """실거래 신고지연 감안, 직전 n개월 YYYYMM."""
    from datetime import date

    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out


def build_localities(out: Path = LOCALITY_FILE) -> "pd.DataFrame":
    """수도권 시군구 입지·가격 수집 → 저평가 랭킹 캐시 (느림: 수 분, 외부 API)."""
    config.load_env()
    if not (config.odsay_analysis_approved() and config.odsay_cache_approved()):
        return pd.DataFrame()  # 경로 분석·결과 저장 허가 전에는 새 캐시를 만들지 않는다
    codes = json.loads(CODES_FILE.read_text(encoding="utf-8")) if CODES_FILE.exists() else {}
    sg = {r: c for r, c in codes.items() if c and c.isdigit() and c[:2] in ("11", "41", "28")}
    rows = locality.build_localities(sg, _recent_months(3))
    df = pd.DataFrame(rows)
    if df.empty:
        return df  # 일시 장애로 기존 정상 캐시를 덮어쓰지 않는다
    df["자료검증버전"] = LOCALITY_MODEL_VERSION
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return df


def load_localities(cache: Path = LOCALITY_FILE) -> "pd.DataFrame":
    if not cache.exists() or not (config.odsay_analysis_approved() and config.odsay_cache_approved()):
        return pd.DataFrame()
    df = pd.read_parquet(cache)
    if "자료검증버전" not in df or "accessibility" not in df:
        return pd.DataFrame()  # 구 버전의 999분·0개 대체 점수는 표시하지 않는다
    return df[(df["자료검증버전"] == LOCALITY_MODEL_VERSION)
              & df["accessibility"].notna() & (df["accessibility"] > -900)]


def load_macro(cache: Path = MACRO_FILE) -> dict:
    return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}


def build_volumes(out: Path = VOLUME_FILE) -> dict:
    """시군구별 월별 거래량 수집 → volume.json (느림: 수 분, 국토부)."""
    config.load_env()
    codes = json.loads(CODES_FILE.read_text(encoding="utf-8")) if CODES_FILE.exists() else {}
    vols = volume.build_volumes(codes, config.public_data_key(), months=24)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(vols, ensure_ascii=False), encoding="utf-8")
    return vols


def load_volumes(cache: Path = VOLUME_FILE) -> dict:
    return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}


def _save(kb: KBWeekly, out: Path) -> KBWeekly:
    out.parent.mkdir(parents=True, exist_ok=True)
    kb.long.to_parquet(out, index=False)
    return kb


def build(xlsx_path: str | Path, out: Path = CACHE_FILE) -> KBWeekly:
    """엑셀을 파싱해 parquet 캐시로 저장하고 KBWeekly 반환."""
    return _save(kb_weekly.load(xlsx_path), out)


def fetch(out: Path = CACHE_FILE, with_supply: bool = True) -> KBWeekly:
    """KB 데이터허브에서 최신 지표(+입주물량)를 받아 캐시로 저장하고 반환."""
    kb = kb_datahub.fetch()
    _save(kb, out)
    if kb.codes:
        CODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        CODES_FILE.write_text(json.dumps(kb.codes, ensure_ascii=False), encoding="utf-8")
    try:
        MACRO_FILE.write_text(json.dumps(kb_datahub.fetch_macro(), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    if with_supply and kb.codes:
        supply = kb_supply.fetch_supply(kb.codes)
        SUPPLY_FILE.parent.mkdir(parents=True, exist_ok=True)
        supply.to_parquet(SUPPLY_FILE, index=False)
    return kb


def load(cache: Path = CACHE_FILE) -> KBWeekly:
    """캐시(parquet)에서 KBWeekly 로드. 없으면 안내."""
    if not cache.exists():
        raise FileNotFoundError(
            f"캐시 없음: {cache}. 먼저 `signal fetch` 또는 `signal build <xlsx>` 로 생성하세요."
        )
    codes = json.loads(CODES_FILE.read_text(encoding="utf-8")) if CODES_FILE.exists() else {}
    return KBWeekly(long=pd.read_parquet(cache), codes=codes)


def load_supply(cache: Path = SUPPLY_FILE) -> pd.DataFrame:
    """입주물량 공급압력 캐시 로드 (없으면 빈 DataFrame)."""
    if not cache.exists():
        return pd.DataFrame(columns=["region", "future_units", "base_units", "supply_pressure"])
    return pd.read_parquet(cache)
