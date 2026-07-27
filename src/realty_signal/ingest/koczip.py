"""개인 확인용 — 콕집(koczip.com) 공개 GET API 클라이언트.

⚠️ 개인·로컬 확인 전용. 호가·할인율은 콕집이 네이버 광고×국토부 실거래를
교차분석한 결과다. 무단 재배포·상업이용·공개 SaaS 프록시 금지(각 사 ToS·
부정경쟁방지법). 저빈도·짧은 TTL 캐시만. 공개 제품으로 전환 시 이 모듈을 폐기하고
자체/제휴 소스로 교체한다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

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

# 콕집 sido 쿼리(10자리) — UI 셀렉트용
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
    """실거래 평균 대비 싼 호가 단지×면적 집계.

    할인율 필드 의미(콕집): discount = (호가 − 실거래평균) / 실거래평균
    → 음수일수록 시세보다 쌈. min_discount=0.05 는 '5% 이상 저렴' 필터.
    """
    return _get("/stats/quick-deals", {
        "sido": sido,
        "sigungu": sigungu,
        "dong": dong,
        "days": days,
        "min_discount": min_discount,
        "max_discount": max_discount,
        "asset": asset,
        "trade_type": trade_type,
        "pyeong": pyeong,
        "min_listings": min_listings,
        "min_samples": min_samples,
        "limit": max(1, min(200, int(limit))),
    })


def fetch_listing_counts(*, asset: str = "all") -> dict:
    """전국·시도별 광고매물/실매물(중복합침) 건수 스냅샷."""
    return _get("/stats/listing-counts", {"asset": asset})


def buyer_discount_pct(item: dict) -> float | None:
    """매수자 관점 최대 할인%(양수=시세보다 쌈). discount_min 이 가장 싼 호가."""
    d = item.get("discount_min")
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
            out.append(f"{parts[i - 1]} {p}")  # 수원시 권선구
        if p.endswith(("구", "시", "군")) and len(p) >= 2:
            out.append(p)
    # 중복 제거, 긴 키 우선
    seen, uniq = set(), []
    for k in sorted(out, key=len, reverse=True):
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq
