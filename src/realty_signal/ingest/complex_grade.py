"""단지단위 급지 — 국토부 실거래 단지별 평단가로 '지역 내 상급/하급'을 산출.

baroezip 급지지도가 하던 일을 우리 공공데이터(국토부 실거래)로 합법 재현한다.
시군구(LAWD 5자리) 내 단지별 평단가 분포를 만들고, 단지가 지역 내 몇 % 상위인지
→ 급지 1~5(1=최상급)로 매핑한다. 시군구 평단가 급지(regime)와 직교하는 '미시 급지'.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET

from realty_signal.auction import _norm, _recent_yms

_BASE = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
_PYEONG = 3.3058  # ㎡ → 평


def _collect(lawd5: str, key: str, months: int = 6) -> list[tuple[str, float]]:
    """(대표 단지명, 평균 평단가) — 평단가 내림차순. 표기차는 정규화로 병합."""
    agg: dict[str, list] = {}  # norm → [대표명, [평단가들]]
    for ym in _recent_yms(months):
        from realty_signal.ingest.complex import _items
        for it in _items(_BASE, lawd5, key, ym):
            try:
                area = float(it.findtext("excluUseAr"))
                amt = float(it.findtext("dealAmount").replace(",", "").strip())
            except (ValueError, AttributeError, TypeError):
                continue
            nm = (it.findtext("aptNm") or "").strip()
            nn = _norm(nm)
            if area <= 0 or not nn:
                continue
            e = agg.setdefault(nn, [nm, []])
            e[1].append(amt / (area / _PYEONG))
    rows = [(e[0], sum(e[1]) / len(e[1])) for e in agg.values() if e[1]]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def _grade(pct: float) -> int:
    """상위 백분위(0=최상) → 급지 1~5."""
    return 1 if pct < 0.10 else 2 if pct < 0.30 else 3 if pct < 0.70 else 4 if pct < 0.90 else 5


def region_grades(lawd5: str, key: str, months: int = 6) -> list[dict]:
    """시군구 단지별 급지 랭킹 — [{단지, 평단가, 급지, 상위, 중앙대비}] (비싼 순)."""
    rows = _collect(lawd5, key, months)
    n = len(rows)
    if not n:
        return []
    med = sorted(p for _, p in rows)[n // 2]
    out = []
    for i, (nm, p) in enumerate(rows):
        out.append({
            "단지": nm, "평단가": round(p), "급지": _grade(i / n),
            "상위": round(i / n * 100),
            "중앙대비": round((p / med - 1) * 100) if med else None,
        })
    return out


def grade_in_region(name: str, ranked: list[dict]) -> dict | None:
    """랭킹에서 단지명에 해당하는 급지 항목 찾기 (정규화 매칭)."""
    cn = _norm(name)
    if not cn or not ranked:
        return None
    for r in ranked:
        rn = _norm(r["단지"])
        if rn == cn or cn in rn or rn in cn:
            return r
    return None


def region_median_pyeong(lawd5: str, key: str, months: int = 6) -> float | None:
    """시군구 단지 평단가 중앙값(만/평) — 청약 분양가 메리트 비교용."""
    rows = _collect(lawd5, key, months)
    if not rows:
        return None
    return round(sorted(p for _, p in rows)[len(rows) // 2])
