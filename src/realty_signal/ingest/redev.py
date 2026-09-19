"""재개발/재건축 — 서울 정비구역(upisRebuild) + 재건축 잠재력 스크리너.

- fetch_zones: 서울 열린데이터 upisRebuild → 정비구역(재건축/재개발 구분·위치·면적).
- rebuild_candidates: 국토부 실거래(연식·평단가) + 건축물대장(용적률·세대수)로
  재건축 잠재력 점수. 오래되고(연식↑)·용적률 낮고·대단지·고시세(일반분양 수익↑) 단지가 유망.

⚠️ 사업단계(조합설립→관리처분) timeline·분담금은 공개 API 부재.
   → 단계 진행/분담금 기반 정밀 ROI 는 가치계산기(사용자 입력)로 보완.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET

from realty_signal.auction import _norm, _recent_yms

_UPIS = "http://openapi.seoul.go.kr:8088"
_RTMS = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
_PYEONG = 3.3058
_NOW_Y = 2026


def _zone_type(sclsf: str) -> str:
    if "재정비촉진" in sclsf:
        return "뉴타운"
    if "재건축" in sclsf:
        return "재건축"
    if "재개발" in sclsf:
        return "재개발"
    if "도시환경" in sclsf:
        return "도시환경"
    if "주거환경" in sclsf:
        return "주거환경"
    return "정비"


def _f(s) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def fetch_zones(seoul_key: str, limit: int = 3000) -> list[dict]:
    """서울 정비구역(재건축/재개발/도시환경) + 재정비촉진(뉴타운) 목록. 구역명+위치 기준 최신 1건만."""
    seen: dict = {}
    for ep, cap in (("upisRebuild", limit), ("upisNewtown", 1000)):
        for start in range(1, cap + 1, 1000):
            end = min(start + 999, cap)
            url = f"{_UPIS}/{seoul_key}/xml/{ep}/{start}/{end}/"
            try:
                root = ET.fromstring(urllib.request.urlopen(  # noqa: S310
                    urllib.request.Request(url, headers=_HDR), timeout=30).read())
            except Exception:
                break
            rows = list(root.iter("row"))
            if not rows:
                break
            for r in rows:
                sclsf = r.findtext("SCLSF") or ""
                z = {
                    "구역명": (r.findtext("RGN_NM") or "").strip(),
                    "위치": (r.findtext("PSTN_NM") or "").strip(),
                    "구분": _zone_type(sclsf),
                    "세부": sclsf,
                    "면적": round(_f(r.findtext("AREA_CHG_AFTR")) or _f(r.findtext("AREA_EXS"))),
                }
                seen[(z["구역명"], z["위치"])] = z
    return list(seen.values())


def _trade_complexes(lawd5: str, key: str, months: int = 6) -> list[dict]:
    """시군구 단지별 {단지명, 연식, 평단가, 지번} — 국토부 실거래 집계."""
    agg: dict = {}
    for ym in _recent_yms(months):
        from realty_signal.ingest.complex import _items
        for it in _items(_RTMS, lawd5, key, ym):
            nm = (it.findtext("aptNm") or "").strip()
            nn = _norm(nm)
            try:
                area = float(it.findtext("excluUseAr"))
                amt = float(it.findtext("dealAmount").replace(",", "").strip())
            except (ValueError, AttributeError, TypeError):
                continue
            by = int(it.findtext("buildYear")) if (it.findtext("buildYear") or "").isdigit() else None
            if not nn or area <= 0:
                continue
            e = agg.setdefault(nn, {"name": nm, "prices": [], "by": by,
                                    "jibun": (it.findtext("umdCd") or "", it.findtext("bonbun") or "",
                                              it.findtext("bubun") or "")})
            e["prices"].append(amt / (area / _PYEONG))
            if by:
                e["by"] = by
    return [{"단지명": e["name"], "연식": e["by"],
             "평단가": round(sum(e["prices"]) / len(e["prices"])), "지번": e["jibun"]}
            for e in agg.values() if e["prices"]]


def _score(age_y: int, vlrat: float | None, hh: int | None, pyeong: int, region_med: int) -> dict:
    """재건축 잠재력 점수(0~100) + 구성요소. 연식↑·용적률↓·대단지·고시세 = 유망."""
    age = max(0.0, min((age_y - 20) / 30, 1.0))                 # 20~50년
    far = max(0.0, min((280 - vlrat) / 180, 1.0)) if vlrat else 0.4  # 200%↓ 유리, 미상=중립
    size = min((hh or 0) / 2000, 1.0)                           # 대단지=사업성
    loc = min(pyeong / (region_med * 1.5), 1.0) if region_med else 0.5  # 고시세=일반분양 수익
    total = age * 0.40 + far * 0.30 + size * 0.15 + loc * 0.15
    return {"잠재력": round(total * 100),
            "_age": round(age * 100), "_far": round(far * 100),
            "_size": round(size * 100), "_loc": round(loc * 100)}


def rebuild_candidates(lawd5: str, key: str, min_age: int = 28) -> list[dict]:
    """시군구 내 재건축 잠재력 단지 랭킹 (구축만). 건축물대장으로 용적률·세대수 보강."""
    from realty_signal.ingest.building import fetch_building

    rows = _trade_complexes(lawd5, key)
    prices = sorted(r["평단가"] for r in rows if r["평단가"])
    region_med = prices[len(prices) // 2] if prices else 0
    out = []
    for r in rows:
        if not r["연식"] or (_NOW_Y - r["연식"]) < min_age:   # 구축(28년+)만
            continue
        umd, bon, bub = r["지번"]
        b = fetch_building(lawd5, umd, bon, bub, key) if bon else None
        vlrat = b["용적률"] if b else None
        hh = b["세대수"] if b else None
        sc = _score(_NOW_Y - r["연식"], vlrat, hh, r["평단가"], region_med)
        out.append({"단지명": r["단지명"], "연식": r["연식"], "연식년": _NOW_Y - r["연식"],
                    "평단가": r["평단가"], "용적률": vlrat, "세대수": hh, **sc})
    out.sort(key=lambda x: x["잠재력"], reverse=True)
    return out


_SIDO5 = {"11": "서울"}  # BIZ_NO 앞 5자리=시군구코드


def fetch_progress(seoul_key: str, max_rows: int = 31000) -> list[dict]:
    """정비사업 추진경과 — BIZ_NO별 단계·날짜 (CleanupBussinessProgress). 전수 페이징."""
    out = []
    for start in range(1, max_rows + 1, 1000):
        end = start + 999
        url = f"{_UPIS}/{seoul_key}/xml/CleanupBussinessProgress/{start}/{end}/"
        try:
            root = ET.fromstring(urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(url, headers=_HDR), timeout=30).read())
        except Exception:
            break
        rows = list(root.iter("row"))
        if not rows:
            break
        for r in rows:
            biz = r.findtext("BIZ_NO") or ""
            day = (r.findtext("DAY") or "").strip()
            se = (r.findtext("SE_NM") or "").strip()
            cd = (r.findtext("SE_CD") or "").strip()
            if biz and se and day.isdigit() and len(day) == 8:
                out.append({"biz": biz, "sgg5": biz.split("-")[0][:5], "단계": se,
                            "cd": int(cd) if cd.isdigit() else 0, "day": day})
    return out


# 정비사업 단계 사다리 (SE_CD 오름차순 ≈ 진행 순서)
_STAGE_ORDER = ["기본계획수립", "정비구역지정", "안전진단", "조합설립추진위원회승인", "조합설립인가",
                "정비사업전문관리업자선정", "설계자선정", "사업시행인가", "시공자선정",
                "관리처분인가", "이주", "철거업자선정", "철거신고", "착공신고",
                "일반분양승인", "준공인가", "이전고시", "조합해산"]


def stage_summary(rows: list[dict], sgg5: str | None = None) -> dict:
    """시군구(sgg5 앞2자리 일치) 정비사업의 현 단계 분포 + 단계 평균 소요기간."""
    by_biz: dict = {}
    for r in rows:
        if sgg5 and r["sgg5"] != sgg5:
            continue
        by_biz.setdefault(r["biz"], []).append(r)
    # 현 단계 = 도달한 최고 단계(SE_CD 최대)
    order = {s: i for i, s in enumerate(_STAGE_ORDER)}
    dist: dict = {}
    durations: dict = {}  # 단계전이 → [일수]
    for biz, evs in by_biz.items():
        evs2 = sorted(evs, key=lambda e: (e["cd"], e["day"]))
        cur = max(evs2, key=lambda e: order.get(e["단계"], e["cd"]))["단계"]
        dist[cur] = dist.get(cur, 0) + 1
        # 단계별 최초 도달일
        first: dict = {}
        for e in evs2:
            first.setdefault(e["단계"], e["day"])
        seq = [s for s in _STAGE_ORDER if s in first]
        for a, b in zip(seq, seq[1:]):
            from datetime import date
            da, db = first[a], first[b]
            d = (date(int(db[:4]), int(db[4:6]), int(db[6:])) - date(int(da[:4]), int(da[4:6]), int(da[6:]))).days
            if 0 < d < 365 * 25:
                durations.setdefault(f"{a}→{b}", []).append(d)
    dist_sorted = [{"단계": s, "사업수": dist[s]} for s in _STAGE_ORDER if s in dist]
    dur_avg = [{"전이": k, "평균개월": round(sum(v) / len(v) / 30.4), "n": len(v)}
               for k, v in durations.items() if len(v) >= 3]
    dur_avg.sort(key=lambda x: _STAGE_ORDER.index(x["전이"].split("→")[0]))
    return {"사업수": len(by_biz), "단계분포": dist_sorted, "평균소요": dur_avg}


def value_calc(현재가: float, 평형: float, 예상분양가_평단: float, 분담금: float,
               보유개월: int = 60) -> dict:
    """재건축 가치 계산 — 입력 기반 ROI.

    현재가(만): 현 매수가, 평형(평): 전용 평수, 예상분양가_평단(만/평): 신축 분양 평단가,
    분담금(만): 추가분담금(감정가·비례율 기반·사용자 추정), 보유개월: 사업 기간 가정.
    """
    신축가치 = round(예상분양가_평단 * 평형)            # 재건축 후 추정 자산가치
    총투입 = round(현재가 + 분담금)
    차익 = 신축가치 - 총투입
    roi = round(차익 / 총투입 * 100, 1) if 총투입 else None
    연환산 = round(roi / (보유개월 / 12), 1) if (roi is not None and 보유개월) else None
    return {"신축추정가치": 신축가치, "총투입": 총투입, "예상차익": 차익,
            "ROI": roi, "연환산ROI": 연환산, "보유개월": 보유개월}
