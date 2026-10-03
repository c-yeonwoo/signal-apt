"""KB 데이터허브 자동 수집 — data-api.kbland.kr 공개 JSON API 직접 호출.

엑셀 수동 다운로드를 대체한다. 표준 라이브러리(urllib)만 사용해
의존성 충돌/세그폴트 없이 동작하며, kb_weekly.load() 와 동일한
tidy long(date, region, metric, value) 형태를 반환한다.

수집 지표(전국 등 24개 광역 단위, 주간):
  매수우위지수(buyer_superiority), 매수세우위(buyer_demand),
  전세수급지수(jeonse_supply), 매매증감률(sale_change), 전세증감률(jeonse_change)
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

import pandas as pd

from realty_signal.ingest.kb_weekly import KBWeekly


def _parse_date(s: str) -> pd.Timestamp:
    """'YYYYMMDD' → Timestamp. pd.to_datetime(format=) 는 일부 파이썬 빌드에서
    스칼라 파싱 시 세그폴트가 나므로 슬라이싱으로 안전하게 구성한다."""
    s = str(s)
    return pd.Timestamp(year=int(s[:4]), month=int(s[4:6]), day=int(s[6:8]))

_BASE = "https://data-api.kbland.kr/bfmstat/weekMnthlyHuseTrnd/"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_WEEKLY = "02"

# 시장동향(maktTrnd) 메뉴코드 → {응답 키: metric}
_TREND = {
    "01": {"매수우위지수": "buyer_superiority", "매수자많음": "buyer_demand"},
    "03": {"전세수급지수": "jeonse_supply"},
}
# 증감률(prcIndxInxrdcRt) 매매전세코드 → metric  (매물종별 01=아파트)
_CHANGE = {"01": "sale_change", "02": "jeonse_change"}

# 수도권 시군구 확장 대상(지역코드) — 증감률만 드릴다운 가능(매수우위/전세수급은 광역만).
# 나머지 지방은 24개 광역 단위 현상유지.
_SUDOGWON = {"1100000000": "서울", "4100000000": "경기", "2800000000": "인천"}

# 지방 개별 추가: {광역 지역코드: {유지할 시군구명}} — 해당 광역을 드릴다운하되 지정 시군구만 보존.
_EXTRA = {"4600000000": {"순천시"}}  # 전남 순천시


def _get(path: str, params: dict) -> dict:
    url = _BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (고정 https 도메인)
        body = json.load(r)
    db = body.get("dataBody", {})
    if str(db.get("resultCode")) != "11000":
        raise RuntimeError(f"KB API 오류({path}): resultCode={db.get('resultCode')}")
    return db["data"]


def _change_rows(
    매매전세코드: str, metric: str, 지역코드: str | None = None, code_sink: dict | None = None
) -> list[tuple]:
    """증감률 엔드포인트 → rows. 지역코드 지정 시 해당 광역의 시군구로 드릴다운.
    code_sink 전달 시 지역명→지역코드(법정동코드) 매핑을 채운다."""
    params = {"월간주간구분코드": _WEEKLY, "매물종별구분": "01", "매매전세코드": 매매전세코드}
    if 지역코드:
        params["지역코드"] = 지역코드
    data = _get("prcIndxInxrdcRt", params)
    dates = [_parse_date(s) for s in data["날짜리스트"]]
    rows = []
    for rec in data["데이터리스트"]:
        region = rec["지역명"]
        # 서울/인천의 중구를 이름만으로 합치면 가격과 지역코드가 서로 다른
        # 지역에서 온다. 기존 서울 표시명은 유지하고 인천을 구분한다.
        region_code = str(rec.get("지역코드") or "")
        if region == "중구" and region_code.startswith("28"):
            region = "인천 중구"
        if code_sink is not None:
            previous = code_sink.get(region)
            if previous and region_code and str(previous) != region_code:
                raise ValueError(f"KB 지역명 코드 충돌: {region} ({previous}, {region_code})")
            if region_code or region not in code_sink:
                code_sink[region] = region_code or None
        for i, v in enumerate(rec["dataList"]):
            if v is not None and i < len(dates):
                rows.append((dates[i], region, metric, float(v)))
    return rows


def fetch_macro() -> dict:
    """거시지표: 전국 아파트 주택구매력지수 + 주택담보대출금리 (월간, 키 불필요).

    주택구매력↑ = 살 여력 좋음(부담↓). 대출금리 = 구입부담의 핵심 변수.
    """
    url = _BASE + "HAI"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        data = json.load(r)["dataBody"]["data"]
    dates = data["날짜리스트"]  # YYYYMM
    power = None
    for rec in data.get("주택구매력리스트", []):
        if rec.get("지역명") == "전국":
            for g in rec.get("지역구분리스트", []):
                if g.get("매물종별구분") == "아파트":
                    power = g.get("매물종별구분리스트")
    rates = [x.get("주택담보대출금리") for x in data.get("주담보월소득리스트", [])]
    return {"dates": dates, "구매력": power, "대출금리": rates}


def fetch(expand_sudogwon: bool = True) -> KBWeekly:
    """KB 데이터허브에서 최신 주간 지표를 받아 KBWeekly 로 반환.

    expand_sudogwon=True 면 수도권(서울·경기·인천)을 시군구 단위로 추가 수집
    (증감률만; 매수우위/전세수급은 광역 단위라 구단위 미제공).
    """
    rows: list[tuple] = []
    codes: dict[str, str] = {}

    # 24개 광역: 매수우위지수·매수세우위·전세수급지수
    for menu, key_map in _TREND.items():
        data = _get("maktTrnd", {"메뉴코드": menu, "월간주간구분코드": _WEEKLY})
        for rec in data["데이터리스트"]:
            region = rec["지역명"]
            for item in rec["dataList"]:
                date = _parse_date(item["기준날짜"])
                for src_key, metric in key_map.items():
                    v = item.get(src_key)
                    if v is not None:
                        rows.append((date, region, metric, float(v)))

    # 24개 광역: 매매·전세 증감률 (+ 지역코드 수집)
    for code, metric in _CHANGE.items():
        rows += _change_rows(code, metric, code_sink=codes)

    # 수도권 시군구 증감률 드릴다운
    if expand_sudogwon:
        for 지역코드 in _SUDOGWON:
            for code, metric in _CHANGE.items():
                rows += _change_rows(code, metric, 지역코드, code_sink=codes)

    # 지방 개별 추가(전남 순천시 등) — 광역 드릴다운 후 지정 시군구만 보존
    for 지역코드, keep in _EXTRA.items():
        for code, metric in _CHANGE.items():
            tmp: dict[str, str] = {}
            all_rows = _change_rows(code, metric, 지역코드, code_sink=tmp)
            rows += [r for r in all_rows if r[1] in keep]
            for r in keep:
                if r in tmp:
                    if codes.get(r) and tmp[r] and codes[r] != tmp[r]:
                        raise ValueError(f"KB 지역명 코드 충돌: {r} ({codes[r]}, {tmp[r]})")
                    codes[r] = tmp[r]

    long = pd.DataFrame(rows, columns=["date", "region", "metric", "value"])
    # 시군구가 광역과 이름이 겹치지 않으나, 혹시 모를 중복(date·region·metric) 제거
    long = long.drop_duplicates(subset=["date", "region", "metric"], keep="last")
    return KBWeekly(long=long, codes=codes, identity_verified=True)
