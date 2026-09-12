"""V-2 국토부 실거래 월별 중위 평단가 수집기.

KB 시그널 검증용으로 시군구·월 단위의 거래별 평단가 중앙값만 저장한다. 원자료를
대시보드에 쓰거나 자동으로 시그널을 바꾸지 않는다. 실패한 월은 캐시하지 않아 다음
실행에서 재시도할 수 있다.
"""

from __future__ import annotations

import json
import statistics
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
_PYEONG_M2 = 3.3058


def month_range(start: str, end: str) -> list[str]:
    """양 끝을 포함한 YYYYMM 목록."""
    if len(start) != 6 or len(end) != 6 or not start.isdigit() or not end.isdigit() or start > end:
        raise ValueError("start/end는 YYYYMM이며 start <= end여야 합니다")
    year, month = int(start[:4]), int(start[4:])
    last_year, last_month = int(end[:4]), int(end[4:])
    if not 1 <= month <= 12 or not 1 <= last_month <= 12:
        raise ValueError("월은 01~12여야 합니다")
    out = []
    while (year, month) <= (last_year, last_month):
        out.append(f"{year}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def target_lawds(codes: dict[str, str]) -> list[str]:
    """광역 코드와 중복을 뺀 시군구 LAWD 5자리 목록."""
    return sorted({code[:5] for code in codes.values()
                   if isinstance(code, str) and len(code) >= 5 and code[:5].isdigit()
                   and code[2:5] != "000"})


def _number(item: ET.Element, tag: str) -> float | None:
    try:
        return float((item.findtext(tag) or "").replace(",", "").strip())
    except (AttributeError, ValueError):
        return None


def monthly_summary(items: list[ET.Element]) -> dict:
    """거래 구성 변화에 민감한 평균 대신 거래별 평단가 중앙값을 쓴다."""
    ppys = []
    for item in items:
        amount, area = _number(item, "dealAmount"), _number(item, "excluUseAr")
        if amount and amount > 0 and area and area > 0:
            ppys.append(amount / (area / _PYEONG_M2))
    return {"transactions": len(ppys), "median_ppy": round(statistics.median(ppys), 2) if ppys else None}


def fetch_month(lawd: str, ym: str, key: str, timeout: int = 30) -> dict | None:
    """한 시군구·월을 페이지 끝까지 읽어 월별 집계를 반환한다. 장애면 None."""
    page, items = 1, []
    total = None
    while True:
        url = f"{_TRADE_URL}?serviceKey={key}&LAWD_CD={lawd}&DEAL_YMD={ym}&numOfRows=999&pageNo={page}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "realty-signal/1.0"})
            root = ET.fromstring(urllib.request.urlopen(request, timeout=timeout).read())  # noqa: S310
        except Exception:  # noqa: BLE001 - failed months must remain eligible for a later retry
            return None
        result_code = root.findtext(".//resultCode")
        if result_code and result_code not in {"00", "000"}:
            return None
        if total is None:
            raw_total = root.findtext(".//totalCount")
            total = int(raw_total) if raw_total and raw_total.isdigit() else 0
        batch = list(root.iter("item"))
        items.extend(batch)
        if not batch or len(items) >= total:
            break
        page += 1
    return {"lawd": lawd, "ym": ym, **monthly_summary(items), "api_total": total}


def _cache_path(cache_dir: Path, lawd: str, ym: str) -> Path:
    return cache_dir / lawd / f"{ym}.json"


def collect(lawds: list[str], months: list[str], key: str, cache_dir: Path, workers: int = 4) -> dict:
    """미수집 월만 병렬 수집한다. 중단해도 다음 실행이 이어받는다."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    pending = [(lawd, ym) for lawd in lawds for ym in months if not _cache_path(cache_dir, lawd, ym).exists()]
    stats = {"cached": len(lawds) * len(months) - len(pending), "fetched": 0, "failed": 0,
             "requested": len(pending), "total": len(lawds) * len(months)}
    if not pending:
        return stats
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_month, lawd, ym, key): (lawd, ym) for lawd, ym in pending}
        for future in as_completed(futures):
            lawd, ym = futures[future]
            try:
                result = future.result()
            except Exception:  # noqa: BLE001
                result = None
            if result is None:
                stats["failed"] += 1
                continue
            path = _cache_path(cache_dir, lawd, ym)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            stats["fetched"] += 1
    return stats
