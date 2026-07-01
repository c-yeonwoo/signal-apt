"""저평가 지역 분석 — 입지(접근성·학군·주거환경) 대비 가격의 헤도닉 잔차.

데이터 소스:
  - 가격(시군구 평단가): 국토부 실거래가 API (PUBLIC_DATA_KEY)        [키]
  - 업무지구 접근성: ODsay 대중교통 소요시간 (ODSAY_KEY)              [키]
  - 학군(학원 밀도): 소상공인 상가정보 API (PUBLIC_DATA_KEY)          [키]
  - 주거환경(공원·하천·마트): OSM Overpass                            [키 불필요]

핵심 엔진 score_undervaluation 은 데이터 소스와 무관하게 순수 함수로 분리해
키 없이도 검증·동작한다. (수집기는 키 활성화 후 연결)
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.parse
import urllib.request

from realty_signal import config

# 주요 업무지구 좌표 (lat, lng)
HUBS = {"강남": (37.4979, 127.0276), "광화문": (37.5759, 126.9769), "여의도": (37.5215, 126.9249)}
_SIDO = {"11": "서울특별시", "41": "경기도", "28": "인천광역시"}


def _fetch(url: str, headers: dict | None = None, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return r.read().decode("utf-8", "replace")


# ---------- 지오코딩: OSM Nominatim (키 불필요, 1req/s) ----------
def geocode(region: str, code: str) -> tuple[float, float] | None:
    sido = _SIDO.get((code or "")[:2], "")
    q = f"{sido} {region}".strip()
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "kr"})
    try:
        data = json.loads(_fetch(url))
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None


# ---------- 가격: 국토부 아파트 매매 실거래가 → 시군구 중위 평단가(만원/평) ----------
def price_per_pyeong(lawd_cd: str, ym_list: list[str]) -> float | None:
    import xml.etree.ElementTree as ET

    key = config.public_data_key()
    base = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    ppp = []
    for ym in ym_list:
        url = f"{base}?serviceKey={key}&LAWD_CD={lawd_cd}&DEAL_YMD={ym}&numOfRows=500&pageNo=1"
        try:
            root = ET.fromstring(_fetch(url))
        except Exception:
            continue
        for it in root.iter("item"):
            try:
                amt = float(it.findtext("dealAmount", "").replace(",", "").strip())  # 만원
                area = float(it.findtext("excluUseAr", "").strip())                  # ㎡
                if amt > 0 and area > 0:
                    ppp.append(amt / (area * 0.3025))   # 만원/평
            except (ValueError, AttributeError):
                continue
    return round(statistics.median(ppp)) if ppp else None


# ---------- 접근성: ODsay 대중교통 소요시간(분), 3개 업무지구 중 최단 ----------
def transit_min(lat: float, lng: float) -> tuple[int, str] | tuple[None, None]:
    """(최단 대중교통 분, 최단 업무지구명). 강남·광화문·여의도 중 가장 가까운 곳."""
    key = config.odsay_key()
    best, best_hub = None, None
    for name, (hlat, hlng) in HUBS.items():
        url = ("https://api.odsay.com/v1/api/searchPubTransPathT?apiKey="
               + urllib.parse.quote(key, safe="")
               + f"&SX={lng}&SY={lat}&EX={hlng}&EY={hlat}")
        try:
            j = json.loads(_fetch(url))
            t = j["result"]["path"][0]["info"]["totalTime"]
            if best is None or t < best:
                best, best_hub = t, name
        except Exception:
            continue
        time.sleep(0.2)
    return (best, best_hub)


def transit_between(sx: float, sy: float, ex: float, ey: float) -> dict | None:
    """두 지점(경도 x, 위도 y) 간 대중교통 최단경로 요약. 키 없거나 실패 시 None."""
    key = config.odsay_key()
    if not key:
        return None
    url = ("https://api.odsay.com/v1/api/searchPubTransPathT?apiKey="
           + urllib.parse.quote(key, safe="")
           + f"&SX={sx}&SY={sy}&EX={ex}&EY={ey}")
    try:
        info = json.loads(_fetch(url))["result"]["path"][0]["info"]
        return {"min": round(info["totalTime"]), "transfer": info.get("busTransitCount", 0) + info.get("subwayTransitCount", 0),
                "pay": info.get("payment")}
    except Exception:
        return None


# ---------- 학군: 소상공인 상가정보, 반경 내 교육(P1) 점포수 ----------
def school_count(lat: float, lng: float, radius: int = 1500) -> int | None:
    key = config.public_data_key()
    url = ("http://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius"
           f"?serviceKey={key}&radius={radius}&cx={lng}&cy={lat}&indsLclsCd=P1"
           "&numOfRows=1&pageNo=1&type=json")
    try:
        return json.loads(_fetch(url)).get("body", {}).get("totalCount")
    except Exception:
        return None

# 입지점수 가중치 (조정 가능)
WEIGHTS = {"accessibility": 0.5, "school": 0.25, "env": 0.25}

_UA = "realty-signal/1.0"


# ---------- 주거환경: OSM Overpass (키 불필요) ----------
_OVERPASS = ["https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter",
             "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]


def osm_environment(lat: float, lng: float, radius: int = 2000) -> dict:
    """반경 내 공원·물(하천/호수)·대형마트 개수. 장애 시 미러 재시도, 끝내 실패하면 0."""
    q = f"""[out:json][timeout:25];
    ( way["leisure"="park"](around:{radius},{lat},{lng});
      way["natural"="water"](around:{radius},{lat},{lng});
      node["shop"="supermarket"](around:{radius},{lat},{lng});
      way["shop"="mall"](around:{radius},{lat},{lng}); );
    out tags;"""
    data = urllib.parse.quote(q)
    for base in _OVERPASS:
        try:
            els = json.loads(_fetch(f"{base}?data={data}",
                             {"User-Agent": _UA, "Accept": "application/json"}, timeout=50)).get("elements", [])
            return {
                "공원": sum(1 for e in els if e.get("tags", {}).get("leisure") == "park"),
                "물": sum(1 for e in els if e.get("tags", {}).get("natural") == "water"),
                "대형마트": sum(1 for e in els if e.get("tags", {}).get("shop") in ("supermarket", "mall")),
            }
        except Exception:
            time.sleep(1.0)
    return {"공원": 0, "물": 0, "대형마트": 0}


# ---------- 저평가 엔진 (순수 함수, 키 불필요) ----------
def _minmax(vals: list[float]) -> list[float]:
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return [50.0 for _ in vals]
    return [(v - lo) / (hi - lo) * 100 for v in vals]


def _linfit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """단순선형회귀 y=a+bx (최소제곱). numpy 없이."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    return my - b * mx, b


def score_undervaluation(rows: list[dict]) -> list[dict]:
    """rows: [{region, price(평단가), accessibility, school, env}] (원점수, 클수록 좋음).

    입지점수 = 가중합(정규화), 적정가 = 입지점수 회귀 예측, 저평가도 = (적정가-실제가)/적정가.
    저평가도 높은 순 정렬.
    """
    rows = [r for r in rows if r.get("price")]
    if len(rows) < 3:
        return rows
    acc = _minmax([r["accessibility"] for r in rows])
    sch = _minmax([r["school"] for r in rows])
    env = _minmax([r["env"] for r in rows])
    for i, r in enumerate(rows):
        r["_acc"], r["_sch"], r["_env"] = round(acc[i]), round(sch[i]), round(env[i])
        r["입지점수"] = round(
            acc[i] * WEIGHTS["accessibility"] + sch[i] * WEIGHTS["school"] + env[i] * WEIGHTS["env"], 1)

    # 가격은 곱셈적 → 로그가격 회귀(헤도닉 표준). 적정가 항상 양수.
    import math

    xs = [r["입지점수"] for r in rows]
    ys = [math.log(r["price"]) for r in rows]
    a, b = _linfit(xs, ys)
    for r in rows:
        fair = math.exp(a + b * r["입지점수"])
        r["적정가"] = round(fair)
        r["저평가도"] = round((fair - r["price"]) / fair * 100, 1)
        r["해설"] = _interpret_locality(r)
    rows.sort(key=lambda r: r["저평가도"], reverse=True)
    return rows


def _interpret_locality(r: dict) -> str:
    """지역별 저평가 해설 — 입지 강점 + 가격 위치를 1~2문장으로."""
    comps = [("업무지구 접근성", r["_acc"]), ("학군(학원 밀도)", r["_sch"]), ("주거환경", r["_env"])]
    comps.sort(key=lambda x: x[1], reverse=True)
    strong, weak = comps[0], comps[-1]
    tmin, hub = r.get("transit_min"), r.get("최단업무지구")
    acc_txt = (f"{hub or '주요 업무지구'}까지 약 {tmin}분(대중교통 최단)" if tmin else "업무지구 접근성")

    lead = f"{acc_txt}, {strong[0]}이(가) 상대적 강점인 지역"
    if strong[1] >= 60 and weak[1] <= 40:
        lead += f" (단, {weak[0]}은(는) 약함)"

    uv = r["저평가도"]
    ratio = round(r["price"] / r["적정가"] * 100) if r["적정가"] else 100
    if uv >= 25:
        tail = f"입지 대비 평단가가 적정가의 {ratio}% 수준으로 **크게 저평가** — 가성비 후보"
    elif uv >= 8:
        tail = f"입지 대비 {ratio}% 수준으로 다소 저평가된 편"
    elif uv <= -25:
        tail = f"입지 대비 평단가가 적정가의 {ratio}% 수준으로 **고평가** — 프리미엄이 충분히 반영됨"
    elif uv <= -8:
        tail = f"입지 대비 {ratio}% 수준으로 다소 고평가된 편"
    else:
        tail = "입지에 가격이 대체로 부합(적정)"
    return f"{lead}. {tail}입니다."


def build_localities(codes: dict, ym_list: list[str], limit: int | None = None) -> list[dict]:
    """수도권 시군구별 가격·접근성·학군·환경 수집 후 저평가 랭킹.

    codes: {지역명: 법정동코드10}. 실거래 없는(집계/시 단위) 지역은 자동 제외.
    """
    config.load_env()
    targets = [(r, c or "") for r, c in codes.items() if (c or "").isdigit() and (c or "")[:2] in _SIDO]
    rows, n, total = [], 0, len(targets)
    for idx, (region, code) in enumerate(targets, 1):
        print(f"  [{idx}/{total}] {region}", flush=True)
        coord = geocode(region, code)
        time.sleep(1.0)  # Nominatim 예의 (1req/s)
        if not coord:
            continue
        lat, lng = coord
        price = price_per_pyeong(code[:5], ym_list)
        if not price:
            continue  # 실거래 없는 집계/시단위 제외
        acc, hub = transit_min(lat, lng)
        sch = school_count(lat, lng)
        env = osm_environment(lat, lng)
        time.sleep(0.4)
        rows.append({
            "region": region, "price": price,
            "transit_min": acc, "최단업무지구": hub, "accessibility": -(acc if acc is not None else 999),
            "school": sch or 0,
            "공원": env["공원"], "물": env["물"], "대형마트": env["대형마트"],
            "env": env["공원"] + env["물"] * 0.5 + env["대형마트"],
        })
        n += 1
        if limit and n >= limit:
            break
    return score_undervaluation(rows)
