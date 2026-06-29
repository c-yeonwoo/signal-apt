"""청약/분양 단지 수집 — KB 분양 API (api.kbland.kr, 키 불필요).

전국 분양(예정·접수·완료) 단지 일정·세대수·분양가. 정비사업(재건축/재개발) 단지도 포함.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date

_URL = "https://api.kbland.kr/land-extra/lots/v1/api/aptSelotInfoList"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _range(months_back: int = 4, months_fwd: int = 18) -> tuple[str, str]:
    y, m = date.today().year, date.today().month
    sb = (y * 12 + (m - 1) - months_back)
    sf = (y * 12 + (m - 1) + months_fwd)
    return f"{sb // 12}{sb % 12 + 1:02d}", f"{sf // 12}{sf % 12 + 1:02d}"


def fetch_presale() -> list[dict]:
    """전국 분양 단지 목록(최근~향후 18개월)."""
    s, e = _range()
    params = {"법정동코드": "0000000000", "임대포함여부": "0", "정렬구분": "2", "정렬순서": "0",
              "조회시작년월": s, "조회종료년월": e, "페이지번호": "1", "페이지목록수": "1000"}
    url = _URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        data = json.load(r)["dataBody"]["data"]
    out = []
    for d in data.get("데이터", []):
        nm = d.get("단지명", "")
        out.append({
            "단지명": nm,
            "주소": d.get("주소", ""),
            "법정동코드": str(d.get("법정동코드", "")),
            "구분": d.get("분양일정구분명", ""),          # 분양계획/청약접수/분양완료 등
            "분양시작": d.get("분양일정시작", ""),
            "분양종료": d.get("분양일정종료", ""),
            "입주": d.get("입주일정", ""),
            "일반세대": d.get("일반세대수"),
            "총세대": d.get("총세대수"),
            "최저분양가": d.get("최저분양가") or None,
            "최대분양가": d.get("최대분양가") or None,
            "정비사업": ("재건축" in nm or "재개발" in nm or "정비사업" in nm or "구역" in nm),
            "lat": d.get("wgs84중심위도"),
            "lng": d.get("wgs84중심경도"),
        })
    return out
