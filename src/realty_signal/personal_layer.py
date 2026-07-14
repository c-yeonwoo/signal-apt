"""개인용 데이터 레이어 — 동네/단지 보조 지표·임장체크·대출 시나리오.

홈에 몰아넣지 않고, 캐시·온디맨드·외부 링크로 피로도를 낮춘다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from realty_signal import store

# 임장 체크 — 수동 확인용. 소음·대기·재해는 공개 지도 링크로 위임(크롤 없음).
IMJANG_CHECKS = [
    {"id": "commute", "label": "출퇴근·통학 동선"},
    {"id": "school", "label": "학군·학원가"},
    {"id": "manage", "label": "관리비·주차·엘리베이터"},
    {"id": "sun", "label": "일조·향·조망"},
    {"id": "noise", "label": "도로·철도·비행 소음"},
    {"id": "air", "label": "대기질"},
    {"id": "hazard", "label": "침수·산사태·재해"},
    {"id": "supply", "label": "인근 입주·분양 일정"},
]


def ext_links(region: str) -> dict[str, str]:
    """지역명 검색 딥링크 — 수치 크롤 대신 공식 지도로 보냄."""
    q = region.replace(" ", "")
    return {
        "생활안전지도": f"https://www.safemap.go.kr/main/smap.do?searchWord={q}",
        "에어코리아": "https://www.airkorea.or.kr/web",
        "통계지리SGIS": "https://sgis.kostat.go.kr/",
        "청약홈": "https://www.applyhome.co.kr/",
    }


def volume_summary(region: str) -> dict[str, Any]:
    vol = store.load_volumes().get(region) or {}
    counts = vol.get("counts") or []
    dates = vol.get("dates") or []
    spark = [c for c in counts[-12:] if c is not None]
    return {
        "거래량비": vol.get("거래량비"),
        "spark": spark,
        "dates": dates[-12:] if dates else [],
        "최근월": counts[-1] if counts else None,
    }


def macro_latest() -> dict[str, Any]:
    path = store.MACRO_FILE
    if not path.exists():
        return {}
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    rates = m.get("대출금리") or []
    hai = m.get("구매력") or []
    dates = m.get("dates") or []
    out: dict[str, Any] = {}
    if rates:
        out["대출금리"] = rates[-1]
    if hai:
        out["구매력"] = hai[-1]
    if dates:
        out["기준"] = dates[-1]
    return out


def locality_bits(lr: dict) -> dict[str, Any]:
    """locality.parquet 행에서 입지 보조 지표."""
    if not lr:
        return {}
    return {
        "학원밀도": lr.get("school"),
        "transit_min": lr.get("transit_min"),
        "최단업무지구": lr.get("최단업무지구"),
        "공원": lr.get("공원"),
        "물": lr.get("물"),
        "대형마트": lr.get("대형마트"),
        "env": lr.get("env"),
    }


def loan_scenarios(capital: float, income: float | None, rate: float,
                   years: int, max_purchase_fn) -> list[dict]:
    """LTV 60/70/80% 시나리오 표. max_purchase_fn = api._max_purchase."""
    rows = []
    for ltv in (0.6, 0.7, 0.8):
        p, detail = max_purchase_fn(capital, ltv, income, rate, years)
        rows.append({
            "ltv": int(ltv * 100),
            "매수가능가": p,
            **detail,
            "라벨": {60: "보수", 70: "기본", 80: "생애최초·여유"}[int(ltv * 100)],
        })
    return rows


def building_for_complex(region: str, name: str, code: str, public_key: str) -> dict | None:
    """단지명 → 최근 실거래 지번 → 건축물대장. kv 캐시 90일."""
    from realty_signal import db
    from realty_signal.auction import recent_trade_price
    from realty_signal.ingest.building import fetch_building

    if not (code and code.isdigit() and public_key):
        return None
    ck = f"building_cx:{code[:5]}:{name}"
    cached = db.kv_get(ck, max_age=90 * 86400)
    if cached is not None:
        return cached or None
    price, by, jibun = recent_trade_price(code[:5], "", name, 0.0, public_key)
    if not jibun or not jibun[1]:
        db.kv_set(ck, {})
        return None
    b = fetch_building(code[:5], jibun[0], jibun[1], jibun[2], public_key)
    if not b:
        db.kv_set(ck, {})
        return None
    if by and not b.get("건축년도"):
        b = {**b, "건축년도": by}
    if price:
        b = {**b, "최근실거래참고": price}
    db.kv_set(ck, b)
    return b


def fav_gongsi_samples(uid: int | None, region: str, limit: int = 3) -> list[dict]:
    """관심단지 중 해당 지역·공시 캐시가 있는 것만 (신규 외부조회 없음)."""
    from realty_signal import db

    if not uid:
        return []
    codes = {}
    try:
        codes = json.loads(store.CODES_FILE.read_text(encoding="utf-8")) if store.CODES_FILE.exists() else {}
    except Exception:  # noqa: BLE001
        codes = {}
    code = codes.get(region) or ""
    result: list[dict] = []
    for f in db.fav_list(uid):
        if f.get("kind") != "complex":
            continue
        key = f.get("key") or ""
        reg, _, nm = key.partition("|")
        if reg != region or not nm:
            continue
        g = db.kv_get(f"gongsi:{region}:{nm}", max_age=90 * 86400) or {}
        if not g.get("㎡단가"):
            continue
        item: dict[str, Any] = {"단지명": nm}
        d = db.kv_get(f"complex:{code[:5]}:{nm}", max_age=30 * 86400) if code[:5].isdigit() else None
        last = (d or {}).get("최근평단가")
        gpy = g["㎡단가"] * 3.3058 / 10000
        if last and gpy:
            item["공시대비"] = round(last / gpy, 2)
        result.append(item)
        if len(result) >= limit:
            break
    return result
