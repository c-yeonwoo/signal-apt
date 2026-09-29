"""K-APT 공식 단지 목록·기본/상세 정보의 보수적인 선택 조회."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

from realty_signal import config, db, jsonx

LIST_URL = "https://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4"
BASIC_URL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5"
DETAIL_URL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusDtlInfoV5"
SOURCE_URL = "https://www.data.go.kr/data/15058453/openapi.do"
LIST_SOURCE_URL = "https://www.data.go.kr/data/15057332/openapi.do"
_ACCESS_ERRORS = (b"SERVICE_KEY_IS_NOT_REGISTERED_ERROR", b"SERVICE_ACCESS_DENIED_ERROR",
                  b"PERMISSION_DENIED")


class KaptAccessError(ValueError):
    """공공데이터포털이 이 서비스에 대한 키 접근을 거부했다."""


def _get(url: str, params: dict) -> dict:
    req = Request(url + "?" + urlencode(params), headers={"User-Agent": "realty-signal/1.0"})
    try:
        with urlopen(req, timeout=8) as response:  # noqa: S310 - fixed official hosts
            raw = response.read(2_000_001)
    except HTTPError as exc:
        # HTTPError의 URL에는 인증키가 있으므로 예외 문자열을 화면이나 로그에 전달하지 않는다.
        error_body = exc.read(4096)
        if any(code in error_body for code in _ACCESS_ERRORS):
            raise KaptAccessError("K-APT service access denied") from None
        raise OSError("K-APT HTTP request failed") from None
    if len(raw) > 2_000_000:
        raise ValueError("K-APT response too large")
    return jsonx.loads(raw)


def _body(raw: dict) -> dict:
    if isinstance(raw, dict) and isinstance(raw.get("response"), dict):
        raw = raw["response"]
    header = raw.get("header") if isinstance(raw, dict) else None
    if not isinstance(header, dict):
        raise ValueError("K-APT header missing")
    if any(code.decode() in str(header.get("resultMsg") or "") for code in _ACCESS_ERRORS):
        raise KaptAccessError("K-APT service access denied")
    if str(header.get("resultCode")) != "00":
        raise ValueError("K-APT service unavailable")
    body = raw.get("body")
    if not isinstance(body, dict):
        raise ValueError("K-APT body missing")
    return body


def _norm(name: object) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", str(name or "").lower())


def _count(value: object) -> int | None:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def _list(sigungu: str, key: str) -> list[dict]:
    cache_key = "kapt:list:" + sigungu
    cached = db.kv_get(cache_key, max_age=86400)
    if isinstance(cached, list):
        return cached
    entries = []
    for page in range(1, 11):
        body = _body(_get(LIST_URL, {"serviceKey": key, "sigunguCode": sigungu,
                                     "pageNo": page, "numOfRows": 1000}))
        items = body.get("items") or []
        if not isinstance(items, list):
            raise ValueError("K-APT list malformed")
        entries.extend(x for x in items if isinstance(x, dict))
        total = _count(body.get("totalCount"))
        if total is None or (not items and len(entries) < total):
            raise ValueError("K-APT list incomplete")
        if len(entries) >= total:
            break
    else:
        # 절단된 목록에서는 단지명이 우연히 유일해 보여도 매칭하지 않는다.
        raise ValueError("K-APT list incomplete")
    db.kv_set(cache_key, entries)
    return entries


def _item(url: str, code: str, key: str, *, key_field: str) -> dict:
    cache_key = "kapt:item:" + url.rsplit("/", 1)[-1] + ":" + code
    cached = db.kv_get(cache_key, max_age=7 * 86400)
    if isinstance(cached, dict):
        return cached
    item = _body(_get(url, {key_field: key, "kaptCode": code})).get("item")
    if not isinstance(item, dict):
        raise ValueError("K-APT item missing")
    db.kv_set(cache_key, item)
    return item


def lookup(name: str, sigungu: str) -> dict:
    """이름+시군구가 유일할 때만 단지 사실을 반환한다."""
    source = {"source": "K-APT", "source_url": SOURCE_URL}
    if not name or not re.fullmatch(r"\d{5}", sigungu or ""):
        return {**source, "status": "unmatched", "reason": "단지명 또는 시군구 코드가 없습니다."}
    key = config.public_data_key()
    if not key:
        return {**source, "status": "unconfigured", "reason": "공공데이터 API 키가 설정되지 않았습니다."}
    key = unquote(key)
    try:
        matches = [item for item in _list(sigungu, key)
                   if _norm(item.get("kaptName")) == _norm(name)
                   and str(item.get("bjdCode") or "").startswith(sigungu)
                   and re.fullmatch(r"A\d+", str(item.get("kaptCode") or ""))]
        if len(matches) != 1:
            return {**source, "status": "ambiguous" if matches else "unmatched",
                    "reason": "동일 이름 단지가 여럿이거나 공식 목록에서 일치 단지를 확인하지 못했습니다."}
        code = matches[0]["kaptCode"]
        basic = _item(BASIC_URL, code, key, key_field="serviceKey")
        if (basic.get("kaptCode") != code or _norm(basic.get("kaptName")) != _norm(name)
                or (basic.get("bjdCode") and not str(basic["bjdCode"]).startswith(sigungu))):
            return {**source, "status": "unmatched", "reason": "공식 상세 정보와 단지 식별자가 일치하지 않습니다."}
        detail = {}
        try:
            candidate = _item(DETAIL_URL, code, key, key_field="ServiceKey")
            if candidate.get("kaptCode") == code and _norm(candidate.get("kaptName")) == _norm(name):
                detail = candidate
        except (OSError, ValueError, TypeError):
            pass
        households = _count(basic.get("kaptdaCnt"))
        surface, underground = _count(detail.get("kaptdPcnt")), _count(detail.get("kaptdPcntu"))
        parking = surface + underground if surface is not None and underground is not None else None
        return {**source, "status": "observed", "kapt_code": code,
                "name": basic.get("kaptName"), "address": basic.get("kaptAddr") or basic.get("doroJuso"),
                "households": households, "builder": basic.get("kaptBcompany") or None,
                "approval_date": basic.get("kaptUsedate") or None,
                "parking_spaces": parking, "parking_per_household": round(parking / households, 2)
                if parking is not None and households else None,
                "parking_status": "observed" if parking is not None else "unavailable",
                "retrieved_at": datetime.now(timezone.utc).date().isoformat(),
                "note": "공식 등록 단지 정보입니다. 주차 가능 여부·시공 품질·실제 출입구는 뜻하지 않습니다."}
    except KaptAccessError:
        return {**source, "status": "approval_required", "action_url": LIST_SOURCE_URL,
                "reason": "K-APT 공식 API 접근이 거부됐습니다. 공공데이터포털에서 단지 목록과 기본 정보 두 서비스의 활용신청·승인 및 운영 키를 확인해 주세요."}
    except (OSError, ValueError, TypeError):
        return {**source, "status": "unavailable", "reason": "K-APT 응답을 확인하지 못했습니다. 활용 승인과 API 상태를 확인해 주세요."}
