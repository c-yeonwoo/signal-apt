"""아파트 매매 거래량(월별 거래건수) 수집 — 국토부 실거래가 totalCount.

원본 룰: '평균 거래량보다 거래량이 커질 때 매수'. 거래량비 = 최근3개월평균 / 직전12개월평균.
시군구 단위만(국토부 LAWD=시군구 5자리). 광역은 N/A.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET


def _yms(n: int) -> list[str]:
    from datetime import date

    y, m, out = date.today().year, date.today().month, []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return list(reversed(out))


def monthly_count(lawd5: str, ym: str, key: str) -> int | None:
    base = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    from realty_signal.ingest.transaction_source import fetch_items
    result = fetch_items(base, lawd5, key, ym)
    return len(result["items"]) if result["status"] == "ok" else None


def build_volumes(codes: dict, key: str, months: int = 24) -> dict:
    """시군구별 월별 거래건수 + 거래량비. {region: {dates, counts, 거래량비}}."""
    yms = _yms(months)
    out = {}
    targets = {r: c for r, c in codes.items()
               if c and c.isdigit() and c[2:5] != "000"}  # 시군구만(광역 제외)
    for region, code in targets.items():
        counts = [monthly_count(code[:5], ym, key) for ym in yms]
        vals = [c for c in counts if c is not None]
        if not vals:
            continue
        recent = [c for c in counts[-3:] if c is not None]
        base = [c for c in counts[-15:-3] if c is not None]
        ratio = (sum(recent) / len(recent)) / (sum(base) / len(base)) if recent and base and sum(base) else None
        out[region] = {
            "dates": [f"{y[:4]}-{y[4:6]}-01" for y in yms],
            "counts": counts,
            "거래량비": round(ratio, 2) if ratio else None,
        }
    return out
