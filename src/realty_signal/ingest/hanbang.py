"""한방 아파트 매매 목록의 개인용 저빈도 수집 어댑터.

공개 모바일 화면의 조회 요청을 사용한다. 원천 응답에는 중개사·개인 연락처가
포함될 수 있으므로 화이트리스트로 정규화한 필드만 호출자에게 돌려준다.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request

from realty_signal import jsonx

BASE = "https://m.karhanbang.com"
SOURCE_URL = f"{BASE}/mptl/main2"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SignalAPT/1.0; personal-listing-watch)",
            "Accept": "application/json", "Referer": SOURCE_URL}
PAGE_SIZE = 30
MAX_PAGES = 3


def _request(path: str, payload: dict | None = None) -> dict:
    headers = {**_HEADERS}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = jsonx.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"{BASE}{path}", data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - 고정 HTTPS 호스트
        data = jsonx.loads(response.read())
    if not isinstance(data, dict) or data.get("code") != 200 or data.get("data") is None:
        raise ValueError("한방 응답 형식 또는 상태가 예상과 다릅니다")
    return data


def _positive(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 1_000_000_000 else None


def normalize(row: dict) -> dict | None:
    """매매 아파트만 보존한다. 원문·연락처·이미지·상세주소는 절대 저장하지 않는다."""
    if row.get("atlfslKndCd") != "01" or row.get("dlngSeCd") != "A1":
        return None
    if row.get("useYn") not in (None, "Y") or row.get("delYn") == "Y":
        return None
    source_id = row.get("atlfslBscInfoPk")
    price = _positive(row.get("trdeAmt"))
    if not source_id or price is None:
        return None
    name = str(row.get("hsmpNm") or "").replace("\\n", "\n").splitlines()[0].strip()
    name = re.sub(r"\s+\d+(?:동|호)$", "", name)
    if not name:
        return None
    area = _positive(row.get("prvuseArea"))
    floor = row.get("flrCnt")
    try:
        floor = int(floor) if floor is not None else None
    except (TypeError, ValueError):
        floor = None
    lat, lng = _positive(row.get("atlfslLat")), _positive(row.get("atlfslLot"))
    if not (lat and 33 <= lat <= 39 and lng and 124 <= lng <= 132):
        lat = lng = None
    return {
        "hanbang_id": str(source_id), "hanbang_complex_id": str(row.get("hsmpInfoPk") or "") or None,
        "단지명": name, "지역": str(row.get("sggNm") or "").strip(),
        "호가": round(price), "전용면적": area, "평형": round(area / 3.3058, 1) if area else None,
        "층": floor, "lat": lat, "lng": lng, "등록일": row.get("atlfslTrsmDt"),
        "급매표시": row.get("atlfslSttsCd") == "02", "검증표시": row.get("atlfslVrfcYn") == "Y",
        "source": "hanbang",
    }


def fetch_region_with_status(lat: float, lng: float, *, max_pages: int = MAX_PAGES,
                             page_size: int = PAGE_SIZE) -> tuple[list[dict], dict]:
    """좌표의 시군구를 아파트 매매로 페이지 조회한다. 제한 도달은 완전 수집이 아니다."""
    try:
        q = urllib.parse.urlencode({"lat": lat, "lng": lng})
        location = _request(f"/mptl/getInitLocInfo?{q}")["data"]["locInfo"]
        if not isinstance(location, dict) or not location.get("sggCdPk"):
            raise ValueError("시군구 코드가 없습니다")
        code = {"ctpv": location["ctpvCdPk"], "sgg": location["sggCdPk"], "emd": 0}
        seen, listings = set(), []
        complete = False
        for page in range(1, max_pages + 1):
            if page > 1:
                time.sleep(0.25)
            payload = {
                "atlfslKndCdList": ["01"],
                "dlngList": [{"types": ["A1"], "trdeMinAmt": 0}],
                "page": {"page": page, "size": page_size}, "rgnCode": [code],
                "atlfslBscInfoPkList": [],
                "searchSort": {"searchOrder": "01", "sortOrder": "DESC"},
                "ctpv": 0, "sgg": 0, "emd": 0,
            }
            groups = _request("/mptl/atlfsl/atlfslList", payload)["data"]
            if not isinstance(groups, dict):
                raise ValueError("매물 목록 형식이 예상과 다릅니다")
            raw_count = 0
            for group in groups.values():
                if not isinstance(group, list):
                    raise ValueError("매물 묶음 형식이 예상과 다릅니다")
                raw_count += len(group)
                for raw in group:
                    if not isinstance(raw, dict):
                        continue
                    item = normalize(raw)
                    if item and item["hanbang_id"] not in seen:
                        seen.add(item["hanbang_id"])
                        listings.append(item)
            if raw_count < page_size:
                complete = True
                break
        return listings, {"ok": True, "pages": page, "complete": complete,
                          "source_sgg": location.get("sggNm"), "source_ctpv": location.get("ctpvNm"),
                          "capped": not complete}
    except Exception as exc:  # noqa: BLE001 - 장애와 정상 0건을 분리한다
        return [], {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
