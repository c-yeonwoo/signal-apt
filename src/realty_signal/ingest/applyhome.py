"""청약홈(한국부동산원) APT 분양정보 — 평형별 분양가·청약일정·특별공급 (data.go.kr 키).

odcloud ApplyhomeInfoDetailSvc:
  - getAPTLttotPblancDetail : 공고 단위 (단지·지역·일정·사업주체)
  - getAPTLttotPblancMdl    : 주택형(평형)별 분양가·공급세대·특공유형  (관리번호로 join)

평형별 분양가(Mdl)는 단지 클릭 시 on-demand 로 가져온다(전부 미리 받지 않음).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/"
_HDR = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}


def _get(ep: str, params: dict) -> dict:
    url = _BASE + ep + "?" + urllib.parse.urlencode(params, safe=":[]")
    return json.loads(urllib.request.urlopen(  # noqa: S310
        urllib.request.Request(url, headers=_HDR), timeout=30).read())


def _sigungu(addr: str) -> str:
    """주소 → 시군구 (시+구 / 시 / 군 / 구). 시도 토큰은 건너뜀."""
    a = re.sub(r"^\S*(특별자치시|특별자치도|특별시|광역시|도)\s*", "", addr or "")
    m = re.search(r"(\S+시\s+\S+구|\S+시|\S+군|\S+구)", a)
    return m.group(1) if m else ""


def fetch_pblanc(since: str) -> list[dict]:
    """모집공고일 >= since(YYYY-MM-DD) APT 분양 공고 목록."""
    params = {"page": 1, "perPage": 500, "serviceKey": _KEY,
              "cond[RCRIT_PBLANC_DE::GTE]": since}
    rows = _get("getAPTLttotPblancDetail", params).get("data", [])
    out = []
    for d in rows:
        addr = d.get("HSSPLY_ADRES", "")
        out.append({
            "관리번호": d.get("HOUSE_MANAGE_NO"),
            "단지명": d.get("HOUSE_NM"),
            "주소": addr,
            "시도": d.get("SUBSCRPT_AREA_CODE_NM"),
            "시군구": _sigungu(addr),
            "주택구분": d.get("HOUSE_SECD_NM"),        # 민영/국민/신혼희망타운 등
            "분양구분": d.get("RENT_SECD_NM"),          # 분양주택/임대
            "총세대": d.get("TOT_SUPLY_HSHLDCO"),
            "모집공고일": d.get("RCRIT_PBLANC_DE"),
            "특공접수시작": d.get("SPSPLY_RCEPT_BGNDE"),
            "특공접수마감": d.get("SPSPLY_RCEPT_ENDDE"),
            "청약접수시작": d.get("RCEPT_BGNDE"),
            "청약접수마감": d.get("RCEPT_ENDDE"),
            "당첨발표": d.get("PRZWNER_PRESNATN_DE"),
            "계약시작": d.get("CNTRCT_CNCLS_BGNDE"),
            "계약종료": d.get("CNTRCT_CNCLS_ENDDE"),
            "입주예정": d.get("MVN_PREARNGE_YM"),
            "사업주체": d.get("BSNS_MBY_NM"),
            "시공사": d.get("CNSTRCT_ENTRPS_NM"),
            "규제지역": d.get("MDAT_TRGET_AREA_SECD"),  # 조정대상지역 Y/N
            "투기과열": d.get("SPECLT_RDN_EARTH_AT"),
            "url": d.get("PBLANC_URL"),
            "홈페이지": d.get("HMPG_ADRES"),
        })
    return out


_SPEC = [("생애최초", "LFE_FRST_HSHLDCO"), ("신혼부부", "NWWDS_HSHLDCO"),
         ("다자녀", "MNYCH_HSHLDCO"), ("노부모", "OLD_PARNTS_SUPORT_HSHLDCO"),
         ("청년", "YGMN_HSHLDCO"), ("기관추천", "INSTT_RECOMEND_HSHLDCO"),
         ("신생아", "NWBB_HSHLDCO")]


def fetch_types(house_manage_no: str) -> list[dict]:
    """단지의 주택형(평형)별 분양가·공급세대·특별공급 유형."""
    params = {"page": 1, "perPage": 100, "serviceKey": _KEY,
              "cond[HOUSE_MANAGE_NO::EQ]": str(house_manage_no)}
    rows = _get("getAPTLttotPblancMdl", params).get("data", [])
    out = []
    for m in rows:
        try:
            분양가 = int(m.get("LTTOT_TOP_AMOUNT") or 0)        # 만원
            면적 = float(m.get("SUPLY_AR") or 0)               # 공급면적 ㎡
        except (ValueError, TypeError):
            continue
        평 = 면적 / 3.3058 if 면적 else 0
        특공 = {nm: int(m.get(f) or 0) for nm, f in _SPEC if int(m.get(f) or 0) > 0}
        out.append({
            "주택형": m.get("HOUSE_TY"),
            "공급면적": round(면적, 1),
            "평": round(평, 1),
            "분양가": 분양가 or None,
            "분양평단가": round(분양가 / 평) if (분양가 and 평) else None,  # 만/평
            "일반세대": int(m.get("SUPLY_HSHLDCO") or 0),
            "특공세대": int(m.get("SPSPLY_HSHLDCO") or 0),
            "특공유형": 특공,
        })
    out.sort(key=lambda x: x["공급면적"])
    return out


# config.public_data_key() 를 모듈 로드시 주입 (api/store 에서 set)
_KEY = ""


def set_key(key: str) -> None:
    global _KEY
    _KEY = key
