"""임장 코스 — 후보 3곳을 '토요일 반나절 일정표'로 내린다.

"저평가 지역은 알겠는데 범위가 넓어 못 간다"가 실제 병목이다. 지역이 아니라
단지를, 그것도 도착·출발 시각이 박힌 순서로 줘야 실행된다.

체크리스트는 데스크에서 확인 가능한 항목(`personal_layer.IMJANG_CHECKS`)과 겹치지 않게
**현장에서만 알 수 있는 것**만 남겼다.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from realty_signal import db

STOP_MIN = 50           # 단지 1곳 체류(단지 한 바퀴 + 중개사 1곳)
MOVE_FALLBACK_MIN = 25  # 대중교통 조회 실패 시 가정
WALK_KM = 1.2           # 이보다 가까우면 도보로 본다
WALK_MIN_PER_KM = 15
DEFAULT_START = "10:00"
MAX_STOPS = 4

# 현장에서만 확인되는 것. 각 항목은 좋음(2)·보통(1)·나쁨(0)으로 기록.
CHECKS = [
    {"id": "walk", "label": "역까지 실측 도보", "hint": "지도 표기 말고 직접 걸어 분 단위로. 경사·신호 포함"},
    {"id": "gap", "label": "동간거리·일조", "hint": "낮에 저층 거실이 어두운지. 앞동에 가리는지"},
    {"id": "parking", "label": "주차", "hint": "세대당 대수보다 저녁 7시 만차 여부. 이중주차 줄"},
    {"id": "manage", "label": "관리 상태", "hint": "외벽 균열·복도 청결·조경·경비실 응대"},
    {"id": "school", "label": "통학로", "hint": "초등학교까지 큰길을 건너는지, 보도 폭"},
    {"id": "living", "label": "생활 상권", "hint": "마트·병원·카페가 도보 10분 안인지. 저녁에도 열려 있는지"},
    {"id": "noise", "label": "소음·냄새", "hint": "간선도로·철도·상가. 창문 열고 1분 서 있어 보기"},
    {"id": "unit", "label": "매물 실물", "hint": "누수 흔적·곰팡이·샷시 연식·수리비 감"},
    {"id": "agent", "label": "중개사 확인", "hint": "실거래 대비 호가, 매물 몇 개, 급한 매도자 있는지"},
    {"id": "feel", "label": "살고 싶은가", "hint": "숫자로 안 잡히는 것. 주민 연령대·분위기"},
]
CHECK_IDS = {c["id"] for c in CHECKS}
VERDICTS = {"계속", "보류", "제외"}
GRADE_MAX = 2


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """두 좌표 사이 직선 km — 방문 순서만 정하면 되므로 근사로 충분."""
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _coords(candidates: list[dict]) -> dict:
    """단지명 → (lat,lng). 실패한 단지는 구 중심으로 대체."""
    from realty_signal import api as app_api
    from realty_signal.ingest.geocode import geocode_batch

    queries = [f"{c['region']} {c['단지']}" for c in candidates]
    got = (geocode_batch(queries) or {}).get("coords") or {}
    out = {}
    for c in candidates:
        key = f"{c['region']} {c['단지']}"
        ll = got.get(key)
        if not ll:
            ll = app_api._region_centroid(c["region"], app_api._code_of(c["region"]))
        if ll:
            out[key] = (float(ll[0]), float(ll[1]))
    return out


def _order(candidates: list[dict], coords: dict, home: tuple[float, float] | None) -> list[dict]:
    """집(없으면 첫 후보)에서 출발하는 최근접 이웃 순회 — 왔던 길을 되돌지 않게."""
    left = list(candidates)
    cur = home
    if cur is None:
        cur = coords.get(f"{left[0]['region']} {left[0]['단지']}")
    out = []
    while left:
        if cur is None:
            out.extend(left)
            break
        left.sort(key=lambda c: _haversine(cur, coords[f"{c['region']} {c['단지']}"])
                  if coords.get(f"{c['region']} {c['단지']}") else 9e9)
        nxt = left.pop(0)
        out.append(nxt)
        cur = coords.get(f"{nxt['region']} {nxt['단지']}") or cur
    return out


def _move_min(a: tuple[float, float] | None, b: tuple[float, float] | None) -> int:
    """구간 대중교통 소요(분). 키 없거나 실패하면 직선거리 기반 추정."""
    if not a or not b:
        return MOVE_FALLBACK_MIN
    km = _haversine(a, b)
    if km < WALK_KM:      # 같은 동네면 대중교통이 더 느리다 — 도보로 잡고 API도 아낀다
        return max(5, round(km * WALK_MIN_PER_KM))
    ckey = f"imjang_leg:{round(a[0], 3)},{round(a[1], 3)}>{round(b[0], 3)},{round(b[1], 3)}"
    cached = db.kv_get(ckey, max_age=30 * 86400)
    if cached:
        return int(cached)
    from realty_signal.ingest import locality
    try:
        r = locality.transit_between(a[1], a[0], b[1], b[0])   # sx=경도, sy=위도
    except Exception:  # noqa: BLE001
        r = None
    m = int(r["min"]) if r and r.get("min") else max(10, round(_haversine(a, b) * 4))
    db.kv_set(ckey, m)
    return m


def _next_saturday(today: date | None = None) -> date:
    d = today or date.today()
    return d + timedelta(days=(5 - d.weekday()) % 7 or 7)


def _hhmm(t: datetime) -> str:
    return t.strftime("%H:%M")


def _map_url(region: str, name: str) -> str:
    from urllib.parse import quote
    return f"https://map.kakao.com/?q={quote(f'{region} {name}')}"


def build_course(candidates: list[dict], *, start: str = DEFAULT_START,
                 stop_min: int = STOP_MIN, home: tuple[float, float] | None = None,
                 visited: dict | None = None, on: str | None = None) -> dict:
    """후보 → 시각이 박힌 코스. visited 는 {region|단지: 방문일} — 이미 본 곳은 뒤로."""
    cands = [c for c in candidates if c.get("단지") and c.get("region")][:MAX_STOPS]
    if not cands:
        return {"ready": False, "reason": "no_candidates"}
    visited = visited or {}
    coords = _coords(cands)
    ordered = _order(cands, coords, home)
    # 이미 다녀온 단지는 순서를 뒤로 — 새 후보를 먼저 보게
    ordered.sort(key=lambda c: 1 if visited.get(f"{c['region']}|{c['단지']}") else 0)

    day = on or _next_saturday().isoformat()
    if not _valid(day, start):
        start = DEFAULT_START
    if not _valid(day, start):
        day, start = _next_saturday().isoformat(), DEFAULT_START
    begin = cur = datetime.strptime(f"{day} {start}", "%Y-%m-%d %H:%M")

    stops, prev_ll, move_total = [], home, 0
    for i, c in enumerate(ordered):
        ll = coords.get(f"{c['region']} {c['단지']}")
        move = _move_min(prev_ll, ll) if prev_ll else 0   # 집 좌표가 없으면 첫 정거장은 0
        if move:
            cur += timedelta(minutes=move)
            move_total += move
        arrive = cur
        cur += timedelta(minutes=stop_min)
        prev_ll = ll or prev_ll
        key = f"{c['region']}|{c['단지']}"
        stops.append({
            "순번": i + 1, "단지": c["단지"], "region": c["region"],
            "이동": move, "도착": _hhmm(arrive), "출발": _hhmm(cur),
            "예상가": c.get("예상가"), "급지": c.get("급지"), "상위": c.get("상위"),
            "시그널": c.get("시그널"), "근거": c.get("근거"),
            "좌표": list(ll) if ll else None, "지도": _map_url(c["region"], c["단지"]),
            "방문일": visited.get(key),
        })
    return {
        "ready": True, "날짜": day, "출발": start, "종료": _hhmm(cur),
        "총소요": int((cur - begin).total_seconds() // 60),
        "이동합": move_total, "체류": stop_min,
        "stops": stops, "checks": CHECKS,
        "준비물": ["신분증", "줄자", "관리비 고지서 요청", "손전등(저층 채광)", "충전기"],
    }


def _valid(day: str, start: str) -> bool:
    try:
        datetime.strptime(f"{day} {start}", "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return True


def score(checks: dict) -> dict | None:
    """체크 결과 → 100점 환산. 응답한 항목만으로 채점(빈칸은 제외)."""
    vals = [v for k, v in (checks or {}).items()
            if k in CHECK_IDS and isinstance(v, (int, float))]
    if not vals:
        return None
    got = sum(max(0, min(GRADE_MAX, int(v))) for v in vals)
    return {"점수": round(got / (len(vals) * GRADE_MAX) * 100), "응답": len(vals),
            "전체": len(CHECKS)}


def weak_points(checks: dict, limit: int = 3) -> list[str]:
    """'나쁨'으로 찍힌 항목 라벨 — 왜 보류·제외인지 기록에 남긴다."""
    labels = {c["id"]: c["label"] for c in CHECKS}
    bad = [labels[k] for k, v in (checks or {}).items()
           if k in labels and isinstance(v, (int, float)) and int(v) <= 0]
    return bad[:limit]


def clean_checks(raw: dict | None) -> dict:
    out = {}
    for k, v in (raw or {}).items():
        if k not in CHECK_IDS:
            continue
        try:
            out[k] = max(0, min(GRADE_MAX, int(v)))
        except (TypeError, ValueError):
            continue
    return out
