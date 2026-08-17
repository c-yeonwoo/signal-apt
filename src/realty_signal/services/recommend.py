"""통합 매물 기반 추천 — listings_all 정규 뷰를 예산·시그널로 거른다.

지역 평단×평형 평균이 아니라, 실제 매물(급매·경매·찐·청약·재건축)을 후보로 쓴다.
기본 풀은 STRONG_BUY. 부족하면 BUY 로 보충한다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from realty_signal.services import shortlist as sl

_SIG_RANK = {"STRONG_BUY": 2, "BUY": 1}
_KIND_BONUS = {"급매": 8, "경매": 6, "찐매물": 5, "청약": 3, "재건축": 2}


def _est_total(row: dict, loc_price: float | None,
               pyeong: float) -> tuple[float | None, str | None]:
    """(총액, 출처). 출처를 함께 돌려주는 이유 —

    지역평단×기준평 근사는 **그 매물의 가격이 아니다.** 같은 필드에 담아 두면
    화면이 근사치를 실제 호가처럼 보여주게 된다. 출처를 붙여 UI 가 구분할 수 있게 한다.
    """
    tot = row.get("총액")
    if tot is not None and tot > 0:
        return float(tot), "매물가"
    if loc_price and pyeong:
        return round(float(loc_price) * float(pyeong)), "지역평단추정"
    return None, None


def score_listing(row: dict, *, budget: float, pyeong: float,
                  loc_price: float | None = None) -> dict | None:
    """단일 매물 점수. 시그널 없으면 제외.

    **가격을 모르는 매물을 '예산 내'로 치지 않는다.** 예전엔 `est is None` 이면 무조건
    통과시키고 기본 적합도 40 을 줬는데, 그 40 은 예산의 56% 짜리 *실제* 매물보다 높은 점수라
    "가격을 모르는 쪽이 유리"해졌다. V2("내 매수력으로 실제로 살 수 있는 집") 정면 위배다.
    이제 가격 미상은 `예산확인필요` 로 분리하고 예산 적합도는 0 이다.
    """
    sig = row.get("시그널") or ""
    if sig not in _SIG_RANK:
        return None
    est, est_src = _est_total(row, loc_price, pyeong)
    unknown_price = est is None
    affordable = bool(est is not None and budget > 0 and est <= budget)
    ratio = (est / budget) if (affordable and est and budget) else 0.0
    budget_fit = sl._budget_score(ratio) if affordable else 0.0
    opp = row.get("기회도") or row.get("타이밍점수") or 0
    kind_b = _KIND_BONUS.get(row.get("유형") or "", 0)
    # 급매갭이 깊을수록(음수) 가산
    gap = row.get("지표값") if row.get("유형") in ("급매", "찐매물") else None
    gap_b = min(12.0, max(0.0, -(float(gap) if gap is not None else 0))) if gap is not None else 0.0
    score = (
        _SIG_RANK[sig] * 10000
        + (1000 if affordable else 0)
        + budget_fit * 50
        + float(opp) * 5
        + kind_b * 10
        + gap_b * 8
    )
    out = dict(row)
    out.update({
        "추정가": est,
        "가격출처": est_src,
        "예산내": affordable,
        "예산확인필요": unknown_price,
        "예산비율": round(ratio, 3) if affordable and est else None,
        "_score": round(score, 1),
    })
    return out


def rank_listings(
    rows: list[dict],
    *,
    budget: float,
    pyeong: float,
    loc_price_of: Callable[[str], float | None],
    prefer_strong: bool = True,
    limit: int = 40,
    max_per_region: int = 3,
) -> list[dict]:
    """통합 매물 → 점수순. **예산이 1차 정렬 기준이고 시그널은 그 다음이다.**

    예전엔 STRONG_BUY 가 3건 이상이면 BUY 풀을 통째로 버렸다(`pool = strong`).
    그런데 STRONG_BUY 는 광역 지표 상속 탓에 특정 시기 특정 광역에 몰린다 —
    실측(2026-08-10)에서 STRONG_BUY 26개 지역이 **전부 서울**이었고, 그 결과
    경기·인천 매물 797건이 예산을 보기도 전에 사라졌다. 예산이 안 되는 사용자에게
    추천이 구조적으로 "못 사는 집" 목록이 된다.

    이제 등급으로 후보를 버리지 않는다. `prefer_strong` 은 **정렬 가중치**로만 남는다
    (`_SIG_RANK` 가 점수의 지배항이므로 예산 내 그룹 안에서 STRONG_BUY 가 여전히 앞선다).
    """
    scored: list[dict] = []
    for r in rows:
        region = r.get("지역") or ""
        s = score_listing(r, budget=budget, pyeong=pyeong,
                          loc_price=loc_price_of(region))
        if s:
            scored.append(s)
    # 정렬 우선순위: ① 예산 내  ② 가격을 아는가  ③ 점수(시그널 지배)
    scored.sort(key=lambda x: (x["예산내"], not x["예산확인필요"], x["_score"]), reverse=True)

    # 한 동네가 목록을 독식하지 않게 — 숏리스트(MAX_PER_REGION=2)와 같은 이유다.
    # 실측에서 노원구 하나가 상위 40건 중 11건(27%)을 차지했다. 같은 동네 11개를 보여주는 건
    # 후보를 넓히는 게 아니라 같은 후보를 반복하는 것이다.
    if max_per_region and max_per_region > 0:
        seen: dict[str, int] = {}
        capped, overflow = [], []
        for x in scored:
            region = x.get("지역") or ""
            n = seen.get(region, 0)
            if n < max_per_region:
                seen[region] = n + 1
                capped.append(x)
            else:
                overflow.append(x)
        # 상한 때문에 자리가 남으면 넘친 것으로 채운다(빈손보다 낫다)
        scored = capped + overflow
    return scored[:limit]


def aggregate_regions(listings: list[dict], *, locmap: dict,
                      budget: float, pyeong: float) -> list[dict]:
    """추천 매물을 지역 카드로 집계 — 대시보드·하위호환용."""
    by: dict[str, list] = defaultdict(list)
    for L in listings:
        if L.get("지역"):
            by[L["지역"]].append(L)
    cards = []
    for region, items in by.items():
        lr = locmap.get(region) or {}
        price = lr.get("price")
        est = round(price * pyeong) if price else None
        sig = items[0].get("시그널") or ""
        # 지역 대표 시그널 = 매물 중 최고 등급
        for it in items:
            s = it.get("시그널") or ""
            if _SIG_RANK.get(s, 0) > _SIG_RANK.get(sig, 0):
                sig = s
        in_budget = [x for x in items if x.get("예산내")]
        affordable = bool(in_budget) or bool(est and est <= budget)
        ratio = (est / budget) if (est and budget and est <= budget) else None
        kinds = defaultdict(list)
        for x in items:
            kinds[x.get("유형") or ""].append(x)
        best = max(items, key=lambda x: x.get("_score") or 0)
        cards.append({
            "region": region, "시그널": sig,
            "평단가": price, "예상매수가": est,
            "예산내": affordable,
            "예산비율": round(ratio, 3) if ratio is not None else None,
            "저평가도": lr.get("저평가도"), "입지점수": lr.get("입지점수"),
            "지역급지": items[0].get("지역급지") or lr.get("급지"),
            "해설": lr.get("해설"),
            "경매단지": [{"단지명": x.get("단지명"), "권장입찰가": x.get("총액"),
                       "시세차익률": x.get("지표값") if x.get("유형") == "경매" else None,
                       "단지급지": None} for x in kinds.get("경매", [])],
            "급매단지": [{"단지명": x.get("단지명"), "평형": (x.get("ref") or {}).get("평형") or x.get("평형"),
                       "호가": x.get("총액"), "급매갭": x.get("지표값")}
                      for x in kinds.get("급매", []) + kinds.get("찐매물", [])],
            "청약단지": [{"단지명": x.get("단지명"), "상태": x.get("지표값"),
                       "Dday": (x.get("ref") or {}).get("Dday"),
                       "관리번호": (x.get("ref") or {}).get("관리번호")}
                      for x in kinds.get("청약", [])],
            "경매건수": len(kinds.get("경매", [])),
            "급매건수": len(kinds.get("급매", [])) + len(kinds.get("찐매물", [])),
            "청약건수": len(kinds.get("청약", [])),
            "매물수": len(items),
            "대표매물": {"유형": best.get("유형"), "단지명": best.get("단지명"),
                      "총액": best.get("추정가") or best.get("총액")},
            "_score": best.get("_score") or 0,
        })
    cards.sort(key=lambda c: (c["예산내"], c["_score"]), reverse=True)
    return cards
