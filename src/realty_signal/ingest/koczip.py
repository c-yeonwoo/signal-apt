"""개인 확인용 — 콕집(koczip.com) 공개 GET API 클라이언트.

⚠️ 개인·로컬 확인 전용. 호가·할인율은 콕집이 네이버 광고×국토부 실거래를
교차분석한 결과다. 무단 재배포·상업이용·공개 SaaS 프록시 금지(각 사 ToS·
부정경쟁방지법). 저빈도·짧은 TTL 캐시만. 공개 제품으로 전환 시 이 모듈을 폐기하고
자체/제휴 소스로 교체한다.

호실 단위는 할인(quick-deals)·특가(special-deals)만 제공됨(전체 광고 덤프 API 없음).
단지 단위 summary 로 호가 밴드·매물수를 보완한다.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

BASE = "https://api.koczip.com"
_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://koczip.com",
    "Referer": "https://koczip.com/quick-deals",
}

SIDO_OPTS = [
    ("", "전국"),
    ("1100000000", "서울"),
    ("4100000000", "경기"),
    ("2800000000", "인천"),
    ("2600000000", "부산"),
    ("2700000000", "대구"),
    ("2900000000", "광주"),
    ("3000000000", "대전"),
    ("3100000000", "울산"),
    ("3600000000", "세종"),
    ("5100000000", "강원"),
    ("4300000000", "충북"),
    ("4400000000", "충남"),
    ("5200000000", "전북"),
    ("4600000000", "전남"),
    ("4700000000", "경북"),
    ("4800000000", "경남"),
    ("5000000000", "제주"),
]

# 스캔 한도 — 저빈도·개인용
MAX_COMPLEX_PER_REGION = 30
SLEEP_SEC = 0.12
SPECIAL_LIMIT = 200
BBOX_DLAT = 0.045
BBOX_DLNG = 0.055


class KoczipError(RuntimeError):
    """콕집 API 거부·파싱 실패."""


def _get(path: str, params: dict | None = None, *, timeout: float = 45) -> dict:
    q = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    url = f"{BASE}{path}"
    if q:
        url = f"{url}?{urllib.parse.urlencode(q)}"
    try:
        raw = urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(url, headers=_HDR), timeout=timeout
        ).read()
    except urllib.error.HTTPError as e:
        body = e.read()[:200].decode("utf-8", errors="replace")
        raise KoczipError(f"HTTP {e.code}: {body}") from e
    except Exception as e:  # noqa: BLE001
        raise KoczipError(str(e)) from e
    text = raw.decode("utf-8", errors="replace")
    if not text.lstrip().startswith(("{", "[")):
        raise KoczipError(text[:180].replace("\n", " "))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise KoczipError(f"JSON 파싱 실패: {text[:120]}") from e
    if not isinstance(data, dict):
        raise KoczipError("예상과 다른 응답 형식")
    return data


def fetch_quick_deals(
    *,
    sido: str | None = None,
    sigungu: str | None = None,
    dong: str | None = None,
    days: int = 90,
    min_discount: float = 0.05,
    max_discount: float = 0.5,
    asset: str = "apt",
    trade_type: str = "A1",
    pyeong: int | None = None,
    min_listings: int = 3,
    min_samples: int = 5,
    limit: int = 60,
) -> dict:
    """실거래 평균 대비 싼 호가 단지×면적 집계."""
    return _get("/stats/quick-deals", {
        "sido": sido, "sigungu": sigungu, "dong": dong,
        "days": days, "min_discount": min_discount, "max_discount": max_discount,
        "asset": asset, "trade_type": trade_type, "pyeong": pyeong,
        "min_listings": min_listings, "min_samples": min_samples,
        "limit": max(1, min(200, int(limit))),
    })


def fetch_listing_counts(*, asset: str = "all") -> dict:
    return _get("/stats/listing-counts", {"asset": asset})


def fetch_complexes_in_bounds(
    swlat: float, swlng: float, nelat: float, nelng: float, *, limit: int = 200,
) -> dict:
    return _get("/complexes/in-bounds", {
        "swlat": swlat, "swlng": swlng, "nelat": nelat, "nelng": nelng,
        "limit": max(1, min(600, int(limit))),
    })


def fetch_complex_summary(complex_no: str) -> dict:
    return _get(f"/complex/{complex_no}/summary")


def fetch_complex_quick_deals(complex_no: str, *, min_discount: float = 0.0) -> dict:
    """단지 내 할인 호실(article). min_discount=0 이면 실거래 대비 싼 쪽 위주 확대."""
    return _get(f"/complex/{complex_no}/quick-deals", {"min_discount": min_discount})


def fetch_special_deals(
    *, asset: str = "apt", days: int = 90, limit: int = 100, offset: int = 0,
) -> dict:
    return _get("/stats/special-deals", {
        "asset": asset, "days": days,
        "limit": max(1, min(300, int(limit))),
        "offset": max(0, int(offset)),
    })


def buyer_discount_pct(item: dict) -> float | None:
    """집계행: discount_min 기준 매수자 할인%(양수=쌈)."""
    d = item.get("discount_min")
    if d is None:
        return None
    try:
        return round(-float(d) * 100, 1)
    except (TypeError, ValueError):
        return None


def article_discount_pct(item: dict) -> float | None:
    """호실행: discount = (호가−실거래평균)/실거래평균 → 양수 할인%."""
    d = item.get("discount")
    if d is None:
        return None
    try:
        return round(-float(d) * 100, 1)
    except (TypeError, ValueError):
        return None


def region_candidates(region_name: str | None) -> list[str]:
    """'서울시 노원구 상계동' → ['노원구', '서울시 노원구'] 등 시그널 키 후보."""
    if not region_name:
        return []
    parts = [p for p in region_name.split()
             if not p.endswith(("동", "읍", "면", "리"))]
    out: list[str] = []
    for i, p in enumerate(parts):
        if p.endswith("구") and i > 0 and parts[i - 1].endswith("시"):
            out.append(f"{parts[i - 1]} {p}")
        if p.endswith(("구", "시", "군")) and len(p) >= 2:
            out.append(p)
    seen, uniq = set(), []
    for k in sorted(out, key=len, reverse=True):
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def region_matches(scan_region: str, region_name: str | None) -> bool:
    if not scan_region or not region_name:
        return False
    a, b = scan_region.replace(" ", ""), region_name.replace(" ", "")
    if a in b or b.endswith(a):
        return True
    for cand in region_candidates(region_name):
        if cand.replace(" ", "") == a:
            return True
    return False


def _won_to_man(v) -> int | None:
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    # 이미 만원 단위로 보이면(너무 작음) 그대로
    if n < 100_000:
        return int(round(n))
    return int(round(n / 10_000))


def sale_band_from_summary(summary: dict) -> tuple[int | None, int | None, int]:
    """by_type 매매(sale_*) 집계 → (min만원, max만원, count)."""
    mins, maxs, cnt = [], [], 0
    for t in summary.get("by_type") or []:
        sc = t.get("sale_count") or 0
        if sc <= 0:
            continue
        cnt += int(sc)
        if t.get("sale_min"):
            mins.append(_won_to_man(t["sale_min"]))
        if t.get("sale_max"):
            maxs.append(_won_to_man(t["sale_max"]))
    mins = [x for x in mins if x]
    maxs = [x for x in maxs if x]
    return (min(mins) if mins else None, max(maxs) if maxs else None, cnt)


def match_signal_region(region_name: str | None, sig: dict) -> tuple[str | None, str | None]:
    for cand in region_candidates(region_name):
        if cand in sig:
            return cand, sig[cand]
        compact = cand.replace(" ", "")
        for r, s in sig.items():
            if r.replace(" ", "") == compact:
                return r, s
    return None, None


def scan_regions(
    regions: list[str],
    *,
    centroid_fn: Callable[[str], tuple[float, float] | None],
    signal_map: dict[str, str],
    max_complexes: int = MAX_COMPLEX_PER_REGION,
) -> dict[str, Any]:
    """BUY+/관심 지역을 스캔해 SQLite에 적재. centroid_fn(region)->(lat,lng)."""
    from realty_signal import db

    stats = {
        "regions": 0, "complexes": 0, "articles_discount": 0,
        "articles_special": 0, "errors": 0, "skipped": 0,
    }
    now = int(time.time())
    region_set = set(regions)
    complex_meta: dict[str, dict] = {}  # complex_no → lat/lng/region/name

    for region in regions:
        cxy = centroid_fn(region)
        if not cxy:
            stats["skipped"] += 1
            continue
        lat, lng = cxy
        db.koczip_clear_region(region)
        stats["regions"] += 1
        try:
            bounds = fetch_complexes_in_bounds(
                lat - BBOX_DLAT, lng - BBOX_DLNG,
                lat + BBOX_DLAT, lng + BBOX_DLNG,
                limit=400,
            )
        except KoczipError as e:
            log.warning("koczip in-bounds %s: %s", region, e)
            stats["errors"] += 1
            continue
        items = list(bounds.get("items") or [])
        items.sort(key=lambda x: x.get("listings") or x.get("c_sale") or 0, reverse=True)
        picked = 0
        for it in items:
            if picked >= max_complexes:
                break
            cno = str(it.get("complex_no") or "")
            if not cno:
                continue
            time.sleep(SLEEP_SEC)
            try:
                sm = fetch_complex_summary(cno)
            except KoczipError as e:
                log.warning("koczip summary %s: %s", cno, e)
                stats["errors"] += 1
                continue
            rname = sm.get("region") or ""
            if not region_matches(region, rname):
                continue
            sig_region, signal = match_signal_region(rname, signal_map)
            use_region = sig_region or region
            smin, smax, scnt = sale_band_from_summary(sm)
            lc = sm.get("listing_counts") or {}
            clat = sm.get("latitude") or it.get("lat")
            clng = sm.get("longitude") or it.get("lng")
            name = sm.get("complex_name") or it.get("name") or cno
            db.koczip_complex_upsert({
                "complex_no": cno, "region": use_region, "name": name,
                "lat": clat, "lng": clng,
                "sale_count": scnt or lc.get("A1"),
                "sale_min": smin, "sale_max": smax,
                "listing_total": lc.get("total") or it.get("listings"),
                "signal": signal or signal_map.get(use_region, ""),
                "raw": json.dumps({
                    "c_sale": it.get("c_sale"), "max_sale": it.get("max_sale"),
                }, ensure_ascii=False),
                "ts": now,
            })
            complex_meta[cno] = {
                "lat": clat, "lng": clng, "region": use_region,
                "name": name, "signal": signal or signal_map.get(use_region, ""),
            }
            stats["complexes"] += 1
            picked += 1

            time.sleep(SLEEP_SEC)
            try:
                qd = fetch_complex_quick_deals(cno, min_discount=0.0)
            except KoczipError as e:
                log.warning("koczip qd %s: %s", cno, e)
                stats["errors"] += 1
                continue
            for art in qd.get("items") or []:
                ano = str(art.get("article_no") or "")
                if not ano:
                    continue
                disc = article_discount_pct(art)
                db.koczip_article_upsert({
                    "article_no": ano, "complex_no": cno, "region": use_region,
                    "kind": "discount", "name": name,
                    "price": _won_to_man(art.get("price")),
                    "area": art.get("area_name"), "floor": art.get("floor_info"),
                    "direction": art.get("direction"), "discount_pct": disc,
                    "naver_url": art.get("naver_url") or art.get("article_url"),
                    "realtor": art.get("realtor_name"), "matched": None,
                    "lat": clat, "lng": clng,
                    "signal": signal or signal_map.get(use_region, ""),
                    "ts": now,
                })
                stats["articles_discount"] += 1

    # 특가 — 전국 일부 받아 region 필터
    try:
        sp = fetch_special_deals(asset="apt", days=90, limit=SPECIAL_LIMIT, offset=0)
        for art in sp.get("items") or []:
            rname = art.get("region_name") or ""
            sig_region, signal = match_signal_region(rname, signal_map)
            if not sig_region or sig_region not in region_set:
                # region_set 키가 '노원구'인데 매칭만 된 경우
                if not any(region_matches(r, rname) for r in region_set):
                    continue
                if not sig_region:
                    for r in region_set:
                        if region_matches(r, rname):
                            sig_region = r
                            signal = signal_map.get(r, "")
                            break
            if not sig_region:
                continue
            cno = str(art.get("complex_no") or "")
            ano = str(art.get("article_no") or "")
            if not ano:
                continue
            meta = complex_meta.get(cno) or {}
            # 좌표 없으면 summary 한 번
            lat, lng = meta.get("lat"), meta.get("lng")
            name = art.get("complex_name") or meta.get("name")
            if (lat is None or lng is None) and cno:
                time.sleep(SLEEP_SEC)
                try:
                    sm = fetch_complex_summary(cno)
                    lat, lng = sm.get("latitude"), sm.get("longitude")
                    name = name or sm.get("complex_name")
                except KoczipError:
                    pass
            db.koczip_article_upsert({
                "article_no": ano, "complex_no": cno, "region": sig_region,
                "kind": "special", "name": name,
                "price": _won_to_man(art.get("price")),
                "area": art.get("area_name"), "floor": art.get("floor_info"),
                "direction": art.get("direction"), "discount_pct": None,
                "naver_url": art.get("naver_url"),
                "realtor": art.get("realtor_name"),
                "matched": art.get("matched"),
                "lat": lat, "lng": lng, "signal": signal or "",
                "ts": now,
            })
            stats["articles_special"] += 1
    except KoczipError as e:
        log.warning("koczip special-deals: %s", e)
        stats["errors"] += 1

    return stats
