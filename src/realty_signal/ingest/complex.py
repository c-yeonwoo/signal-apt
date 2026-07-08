"""단지 deep-dive — 국토부 실거래(매매+전세)로 단지별 시세 추이·평형별·전세가율·갭.

거래 직전 의사결정의 마지막 단계: "이 단지, 이 평형, 지금 얼마/전세가율/갭".
매매=RTMSDataSvcAptTradeDev, 전세=RTMSDataSvcAptRent (둘 다 시군구·월 단위 → 단지명 매칭).
"""

from __future__ import annotations

import difflib
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from realty_signal.auction import _norm, _recent_yms

_TRADE = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
_RENT = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
_PYEONG = 3.3058


def _items_parallel(base: str, lawd5: str, key: str, yms: list[str]) -> list:
    """여러 월(ym)의 실거래를 병렬 조회 — 순차 36콜(수십초)을 몇 초로 단축."""
    out: list = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for items in ex.map(lambda ym: _items(base, lawd5, key, ym), yms):
            out.extend(items)
    return out


def _items(base: str, lawd5: str, key: str, ym: str) -> list:
    url = f"{base}?serviceKey={key}&LAWD_CD={lawd5}&DEAL_YMD={ym}&numOfRows=900&pageNo=1"
    try:
        root = ET.fromstring(urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(url, headers=_HDR), timeout=12).read())
    except Exception:
        return []
    return list(root.iter("item"))


# 국토부 구표기 ↔ 통용 표기 철자 통일(정규화로 흡수 → 별개 단지 오매칭 없이 변형만 일치)
_SPELL = {"맨숀": "맨션", "빠트": "파트", "아빠트": "아파트"}
_ROMAN = {"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5", "Ⅵ": "6"}


def _canon(s: str) -> str:
    """비교용 표준형 — 로마숫자→아라비아, _norm(한글·숫자만), 철자변형 통일."""
    for a, b in _ROMAN.items():
        s = s.replace(a, b)
    n = _norm(s)
    for a, b in _SPELL.items():
        n = n.replace(a, b)
    return n


def _match(nm: str, target_canon: str) -> bool:
    """단지명 매칭 — 표준형 정확·부분포함이 기본. 철자변형은 정규화로 흡수.

    숫자(동·단지 번호)가 양쪽에 있고 다르면 불일치(주공1 vs 주공10 방지),
    그 외 미세 변형만 높은 유사도(0.9)로 안전 허용(다성/주성 같은 별개 단지는 배제).
    """
    n = _canon(nm)
    if not n:
        return False
    if n == target_canon or target_canon in n or n in target_canon:
        return True
    dn, dt = re.findall(r"\d+", n), re.findall(r"\d+", target_canon)
    if dn and dt and dn != dt:
        return False
    return difflib.SequenceMatcher(None, n, target_canon).ratio() >= 0.9


def _amt(it, tag: str) -> float | None:
    try:
        return float((it.findtext(tag) or "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _ym_of(it) -> str:
    y, m = (it.findtext("dealYear") or "").strip(), (it.findtext("dealMonth") or "").strip()
    return f"{y}-{int(m):02d}" if y and m.isdigit() else ""


def fetch_complex(lawd5: str, apt_name: str, key: str,
                  trade_months: int = 24, rent_months: int = 12) -> dict:
    """단지 실거래 종합. {매매추이(월별 평단가)·평형별(매매·전세·전세가율·갭)·요약}."""
    cn = _canon(apt_name)
    if not cn:
        return {}
    trades: list[dict] = []
    for it in _items_parallel(_TRADE, lawd5, key, _recent_yms(trade_months)):
        if not _match(it.findtext("aptNm") or "", cn):
            continue
        area, amt = _amt(it, "excluUseAr"), _amt(it, "dealAmount")
        if not area or area <= 0 or not amt:
            continue
        trades.append({"ym": _ym_of(it), "area": area, "amt": amt,
                       "pyeong": round(area / _PYEONG), "ppy": amt / (area / _PYEONG)})
    rents: list[dict] = []
    for it in _items_parallel(_RENT, lawd5, key, _recent_yms(rent_months)):
        if not _match(it.findtext("aptNm") or "", cn):
            continue
        mr = _amt(it, "monthlyRent") or 0
        if mr > 0:  # 전세만 (월세 제외)
            continue
        area, dep = _amt(it, "excluUseAr"), _amt(it, "deposit")
        if not area or area <= 0 or not dep:
            continue
        rents.append({"ym": _ym_of(it), "area": area, "pyeong": round(area / _PYEONG), "deposit": dep})
    if not trades:
        return {"단지명": apt_name, "매매추이": [], "평형별": [], "거래없음": True}

    # 월별 평균 평단가(전체 추이)
    by_ym: dict = {}
    for t in trades:
        if t["ym"]:
            by_ym.setdefault(t["ym"], []).append(t["ppy"])
    추이 = [{"ym": k, "평단가": round(sum(v) / len(v)), "건수": len(v)} for k, v in sorted(by_ym.items())]

    # 평형별 집계 (최근 매매 + 전세 → 전세가율·갭)
    def _recent(rows, kk):
        return sorted(rows, key=lambda r: r["ym"])[-1][kk] if rows else None

    pys = sorted({t["pyeong"] for t in trades})
    평형별 = []
    for py in pys:
        ts = [t for t in trades if t["pyeong"] == py]
        rs = [r for r in rents if r["pyeong"] == py]
        recent_amt = _recent(ts, "amt")
        recent_dep = _recent(rs, "deposit")
        jeonse_ratio = round(recent_dep / recent_amt * 100) if (recent_amt and recent_dep) else None
        평형별.append({
            "평형": py, "전용㎡": round(sum(t["area"] for t in ts) / len(ts), 1),
            "최근매매": recent_amt and round(recent_amt), "평단가": round(sum(t["ppy"] for t in ts) / len(ts)),
            "매매건수": len(ts), "최근전세": recent_dep and round(recent_dep),
            "전세가율": jeonse_ratio, "갭": (recent_amt and recent_dep) and round(recent_amt - recent_dep),
        })
    평형별.sort(key=lambda x: x["평형"])

    last = 추이[-1]["평단가"] if 추이 else None
    first = 추이[0]["평단가"] if 추이 else None
    return {
        "단지명": apt_name, "매매추이": 추이, "평형별": 평형별,
        "최근평단가": last, "총거래": len(trades),
        "추세pct": round((last / first - 1) * 100, 1) if (first and last) else None,
        "기간": f"{추이[0]['ym']}~{추이[-1]['ym']}" if 추이 else None,
    }
