"""선택 매물 주변의 대안과 기존 뉴스 헤드라인을 보수적으로 연결한다.

뉴스 문자열 일치는 개발사업의 존재·진행 단계를 증명하지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from urllib.parse import urlparse

from realty_signal.services import listing_watch
from realty_signal.services.property_analysis import snapshot


def _positive(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if isfinite(n) and n > 0 else None


def _same_complex(a: dict, b: dict) -> str | None:
    if a.get("지역") != b.get("지역") or not a.get("단지명") or a.get("단지명") != b.get("단지명"):
        return None
    ar, br = a.get("ref") or {}, b.get("ref") or {}
    for field in ("hanbang_complex_id", "complex_no"):
        if ar.get(field) and br.get(field):
            return "source_id" if str(ar[field]) == str(br[field]) else "conflict"
    return "name_region_only"


def alternatives(row: dict, candidates: list[dict], budget: float | None = None) -> list[dict]:
    """동일 원천 매물을 중복시키지 않고, 확정 못 한 단지 동일성은 별도 표기한다."""
    src = snapshot(row)
    if not src["asking_manwon"]:
        return []
    seen = set()
    scored = []
    for candidate in candidates:
        key = candidate.get("key")
        if not key or key == src["key"] or key in seen or listing_watch._same_source_listing(row, candidate):
            continue
        seen.add(key)
        if candidate.get("유형") not in {"일반매물", "급매", "찐매물"}:
            continue
        other = snapshot(candidate)
        if not other["asking_manwon"]:
            continue
        identity = _same_complex(row, candidate)
        if identity == "conflict":
            continue
        same_area = bool(src["exclusive_m2"] and other["exclusive_m2"] and
                         0.85 <= other["exclusive_m2"] / src["exclusive_m2"] <= 1.15)
        same_price = 0.8 <= other["asking_manwon"] / src["asking_manwon"] <= 1.2
        if identity:
            reason = "같은 원천 단지 ID" if identity == "source_id" else "같은 지역·단지명(동일 단지 확인 필요)"
            group = 0
        elif other["region"] == src["region"] and same_area and same_price:
            reason = "같은 지역·비슷한 전용면적·호가대"
            group = 1
        else:
            continue
        scored.append((group, bool(other["stale"]), abs(other["asking_manwon"] - src["asking_manwon"]),
                       {"listing": other, "reason": reason,
                        "budget_fit": ("within" if other["asking_manwon"] <= budget else "above") if budget else "unknown"}))
    scored.sort(key=lambda x: x[:3])
    same = [x[3] for x in scored if x[0] == 0][:3]
    nearby = [x[3] for x in scored if x[0] == 1][:max(0, 6-len(same))]
    return same + nearby


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.strptime(value, "%a, %d %b %Y").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None


def _https(url: str | None) -> str | None:
    if not isinstance(url, str) or len(url) > 1000:
        return None
    parsed = urlparse(url)
    return url if parsed.scheme == "https" and parsed.hostname else None


def news(row: dict, articles: list[dict], *, now: datetime | None = None) -> list[dict]:
    """직접 관련이라고 주장하지 않는 문자열 매칭. 오래된 기사와 비HTTPS는 제외."""
    now = now or datetime.now(timezone.utc)
    name, region = (row.get("단지명") or "").strip(), (row.get("지역") or "").strip()
    # '자이', 'e편한세상' 같은 짧고 흔한 표기를 단지 고유명으로 오인하지 않는다.
    name = name.replace("아파트", "").strip()
    found = []
    for article in articles:
        published, link = _published(article.get("pubdate")), _https(article.get("link"))
        if not published or not link or not 0 <= (now - published).days <= 180:
            continue
        title, descr = str(article.get("title") or "")[:180], str(article.get("descr") or "")[:260]
        combined = title + " " + descr
        name_match = len(name) >= 4 and name in combined
        region_match = len(region) >= 2 and region in combined
        if not name_match and not region_match:
            continue
        level = "단지명·지역명 문자열 일치" if name_match and region_match else (
            "단지명 문자열 일치(소속 미확인)" if name_match else "지역명 문자열 일치(단지 관련 미확인)")
        found.append((0 if name_match and region_match else 1 if name_match else 2,
                      -published.timestamp(), {"title": title, "snippet": descr, "url": link,
                       "source": article.get("source"), "published_at": published.date().isoformat(),
                       "match": level, "verified_project": False}))
    found.sort(key=lambda x: x[:2])
    return [item for _, _, item in found[:6]]
