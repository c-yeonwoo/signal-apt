"""외부 사이트 목록 수집의 한 입구.

전송은 Transport 하나다. 지금은 프로세스의 직접 HTTP이고,
EXTERNAL_HTTP_PROXY 가 있으면 그 프록시만 탄다. 나중에 프록시 주소를
이 환경변수에 넣으면 된다. 로그인 세션이나 토큰 목록은 저장하지 않는다.

기본 수집은 행크 진행 아파트다. 부동산지인 시장강도는 점수 칸이 비어
여기 기본 수집에 넣지 않는다.
"""

from __future__ import annotations

import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from realty_signal import jsonx, store

_UA = "Mozilla/5.0"
_HANK = "https://api.hauction.co.kr/api/v1"
_HANK_REGIONS = ("서울", "경기")
_HANK_TTL = 12 * 3600
_LOCK = threading.Lock()
_OPEN = {"진행", "신건", "유찰"}
_REVIEW = (
    "비교할 시세와 실거래가가 없습니다",
    "등기부, 매각물건명세서, 점유를 확인하기 전입니다",
    "면적은 건물면적이며 전용면적이 아닙니다",
)


class FetchError(RuntimeError):
    pass


class DirectTransport:
    """표준 라이브러리 GET. proxy 가 없으면 환경변수 EXTERNAL_HTTP_PROXY 를 본다."""

    def __init__(self, proxy: str | None = None, timeout: float = 30):
        self.proxy = proxy if proxy is not None else os.environ.get("EXTERNAL_HTTP_PROXY") or None
        self.timeout = timeout

    def get_json(self, url: str) -> Any:
        handlers = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json",
                                                    "X-HANK-PLATFORM": "web"})
        try:
            with opener.open(req, timeout=self.timeout) as res:
                return jsonx.loads(res.read().decode())
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise FetchError(type(exc).__name__) from exc


@dataclass
class SourceResult:
    name: str
    ok: bool
    rows: list[dict] = field(default_factory=list)
    skipped: bool = False
    error: str = ""
    truncated: bool = False

    @property
    def count(self) -> int:
        return len(self.rows)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def hank_row(raw: dict, region: str) -> dict | None:
    """지도 목록 한 건을 우리 좌표 순서로 옮긴다. latlng 가 없으면 버린다."""
    ll = raw.get("latlng")
    if not isinstance(ll, (list, tuple)) or len(ll) != 2:
        return None
    lng, lat = _num(ll[0]), _num(ll[1])
    if lat is None or lng is None or not (33 <= lat <= 39 and 124 <= lng <= 132):
        return None
    return {
        "source": "hank",
        "region": region,
        "id": raw.get("id"),
        "unique_id": raw.get("unique_id") or "",
        "category": raw.get("category") or "",
        "status": raw.get("status") or "",
        "department": raw.get("department") or "",
        "apsl_amount": raw.get("apsl_amount"),
        "minb_amount": raw.get("minb_amount"),
        "minb_rate": raw.get("minb_rate"),
        "fb_count": raw.get("fb_count"),
        "bid_dttm": raw.get("bid_dttm") or "",
        "bldg_sqm": raw.get("bldg_sqm"),
        "special_condition": raw.get("special_condition") or "",
        "address": raw.get("address") or "",
        "lat": lat,
        "lng": lng,
    }


def collect_hank(transport: DirectTransport, *, regions: tuple[str, ...] = _HANK_REGIONS,
                 page_size: int = 100, max_pages: int = 30) -> SourceResult:
    """진행 중 아파트, 유찰 1회 이하. 감정가 상한은 여기서 자르지 않는다."""
    rows: list[dict] = []
    seen: set = set()
    truncated = False
    for region in regions:
        for page in range(1, max_pages + 1):
            query = urllib.parse.urlencode({
                "page": page, "page_size": page_size, "type": "real",
                "category": "아파트", "addr1": region, "status": "진행", "failure_max": 1,
            })
            data = transport.get_json(f"{_HANK}/auction/search/map?{query}")
            batch = data.get("results") or []
            for raw in batch:
                row = hank_row(raw, region)
                if not row:
                    continue
                key = row.get("id")
                if key is not None and key in seen:
                    continue
                if key is not None:
                    seen.add(key)
                rows.append(row)
            if not data.get("next"):
                break
            if page == max_pages:
                truncated = True
    return SourceResult(name="hank", ok=True, rows=rows, truncated=truncated)


APT_GIN_REASON = (
    "비로그인 시장강도 응답에는 거래량·세대수 칸만 있고 "
    "TRADE·RENT_SCORE·시세 칸은 비어 있다. 날짜 이동은 로그인 창이다. 수집하지 않는다."
)


def collect_aptgin(_transport: DirectTransport) -> SourceResult:
    return SourceResult(name="aptgin", ok=False, skipped=True, error=APT_GIN_REASON)


_SOURCES: dict[str, Callable[[DirectTransport], SourceResult]] = {
    "hank": collect_hank,
    "aptgin": collect_aptgin,
}


def collect(names: list[str] | None = None, transport: DirectTransport | None = None) -> list[SourceResult]:
    """이름을 생략하면 행크만 수집한다. 지인은 이름을 지정해도 건너뛴다."""
    chosen = names or ["hank"]
    unknown = [name for name in chosen if name not in _SOURCES]
    if unknown:
        raise FetchError("unknown source: " + ",".join(unknown))
    client = transport or DirectTransport()
    results = []
    for name in chosen:
        try:
            results.append(_SOURCES[name](client))
        except FetchError as exc:
            results.append(SourceResult(name=name, ok=False, error=str(exc)))
    return results


def cache_path():
    return store.CACHE_DIR / "external_hank.json"


def write_hank_cache(result: SourceResult, path=None) -> None:
    target = path or cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "hank",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": result.count,
        "truncated": result.truncated,
        "rows": result.rows,
    }
    target.write_text(jsonx.dumps(payload), encoding="utf-8")


def manwon(value: Any) -> int | None:
    """행크 금액(원)을 매물 카드 단위(만원)로."""
    amount = _num(value)
    if amount is None or amount <= 0:
        return None
    return int(round(amount / 10000))


def _text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(part for item in value if (part := str(item).strip()))
    return str(value or "").strip()


_SIDO = (
    ("서울특별시", "서울"), ("서울시", "서울"), ("서울", "서울"),
    ("경기도", "경기"), ("경기", "경기"),
    ("인천광역시", "인천"), ("인천시", "인천"), ("인천", "인천"),
)


def parse_place(address: str) -> dict:
    """주소에서 화면 지역명을 뽑는다. 시군구가 없으면 지역은 빈다."""
    text = " ".join((address or "").split())
    sido, rest = "", text
    for prefix, label in _SIDO:
        if text.startswith(prefix):
            sido, rest = label, text[len(prefix):].strip()
            break
    si_gu = re.match(r"^(\S+시)\s+(\S+구)(?:\s+(\S+[동읍면]))?", rest)
    gu = re.match(r"^(\S+구)(?:\s+(\S+[동읍면]))?", rest)
    gun = re.match(r"^(\S+군)(?:\s+(\S+[읍면동]))?", rest)
    si = re.match(r"^(\S+시)(?:\s+(\S+[동읍면]))?", rest)
    if si_gu:
        region, dong = f"{si_gu.group(1)} {si_gu.group(2)}", si_gu.group(3) or ""
    elif gu:
        region, dong = gu.group(1), gu.group(2) or ""
    elif gun:
        region, dong = gun.group(1), gun.group(2) or ""
    elif si:
        region, dong = si.group(1), si.group(2) or ""
    else:
        region, dong = "", ""
    return {"시도": sido, "지역": region, "동": dong}


_ROAD_PAREN = re.compile(r"\s*\((?:서울|경기|인천|부산|대구|광주|대전|울산|세종|제주)[^)]*\)")
_NAME_PAREN = re.compile(r"\(([^)]+)\)")
_UNIT_CLAUSE = re.compile(
    r"제?[0-9]+동|제[가-힣]{1,4}동|[0-9]+층[0-9]*호|제?[0-9]+층|제?[0-9]+호|[0-9]+-[0-9]+호"
)
_LOT = re.compile(r"^[0-9]+(?:-[0-9]+)?$")


def complex_name(address: str) -> str:
    """목록 제목에 쓸 단지명. 없으면 빈 문자열. 사건번호는 넣지 않는다."""
    text = " ".join((address or "").split())
    for match in _NAME_PAREN.finditer(text):
        inner = match.group(1).strip()
        if re.match(r"^(서울|경기|인천|부산|대구|광주|대전|울산|세종|제주)", inner):
            continue
        if "," in inner:
            name = inner.split(",")[-1].strip()
            if name:
                return name
    head = _ROAD_PAREN.sub("", text)
    head = re.sub(r"\s*외\s*[0-9]+필지.*$", "", head)
    head = _UNIT_CLAUSE.sub(" ", head).replace(",", " ")
    tokens = [token for token in head.split() if token and token != "외"]
    lot_at = None
    for index, token in enumerate(tokens):
        if _LOT.match(token):
            lot_at = index
    if lot_at is None:
        return ""
    return " ".join(tokens[lot_at + 1:]).strip()


def card_from_hank(row: dict) -> dict | None:
    """목록 한 건을 매물 카드 필드로. 권장 입찰가와 전용면적은 넣지 않는다."""
    lat, lng = _num(row.get("lat")), _num(row.get("lng"))
    if lat is None or lng is None:
        return None
    status = _text(row.get("status"))
    if status and status not in _OPEN:
        return None
    place = parse_place(row.get("address") or "")
    special = _text(row.get("special_condition"))
    case = _text(row.get("unique_id"))
    title = complex_name(row.get("address") or "") or place["동"] or "아파트"
    hid = row.get("id")
    if hid not in (None, ""):
        ident = f"hank:{hid}"
    elif case:
        ident = f"hank:{case}"
    else:
        return None
    area = _num(row.get("bldg_sqm"))
    bid = _text(row.get("bid_dttm"))[:10]
    reasons = ([f"목록의 특수조건: {special}"] if special else []) + list(_REVIEW)
    return {
        "source": "hank",
        "id": ident,
        "사건번호": case,
        "단지명": title,
        "region": place["지역"],
        "시도": place["시도"],
        "동": place["동"],
        "주소": _text(row.get("address")),
        "감정가": manwon(row.get("apsl_amount")),
        "최저매각가": manwon(row.get("minb_amount")),
        "유찰횟수": row.get("fb_count"),
        "입찰기일": bid or None,
        "입찰상태": "needs_review",
        "확인할것": reasons,
        "권장입찰가": None,
        "목표달성": False,
        "건물면적": area if area and area > 0 else None,
        "특수조건": special,
        "lat": lat,
        "lng": lng,
        "category": _text(row.get("category")),
        "status": status,
    }


def cards_from_rows(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for row in rows:
        card = card_from_hank(row)
        if not card or card["id"] in seen:
            continue
        seen.add(card["id"])
        out.append(card)
    return out


def _read_cache(path=None) -> dict | None:
    target = path or cache_path()
    if not target.exists():
        return None
    try:
        data = jsonx.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _age_seconds(payload: dict) -> float | None:
    raw = payload.get("fetched_at")
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def hank_due(path=None) -> bool:
    payload = _read_cache(path)
    if not payload or not payload.get("rows"):
        return True
    age = _age_seconds(payload)
    return age is None or age > _HANK_TTL


def read_hank_cards() -> list[dict]:
    """캐시만 읽는다. 테스트 중에는 로컬 캐시가 매물 수를 바꾸지 않게 빈 목록이다."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return []
    payload = _read_cache()
    return cards_from_rows((payload or {}).get("rows") or [])


def refresh_hank_cache(transport: DirectTransport | None = None, *, path=None, force: bool = False) -> dict:
    """진행 아파트 목록을 다시 받아 캐시에 쓴다. 빈 결과는 기존 캐시를 덮지 않는다."""
    with _LOCK:
        if transport is None and path is None and not force and not hank_due():
            payload = _read_cache() or {}
            return {"ok": True, "count": len(payload.get("rows") or []), "refreshed": False}
        result = collect(["hank"], transport=transport)[0]
        if not result.ok or not result.rows:
            raise FetchError(result.error or "empty")
        write_hank_cache(result, path)
        return {"ok": True, "count": result.count, "refreshed": True}


def ensure_hank_cache() -> None:
    """요청 경로용. 테스트에서는 네트워크를 열지 않는다."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if not hank_due():
        return
    try:
        refresh_hank_cache()
    except FetchError:
        return
