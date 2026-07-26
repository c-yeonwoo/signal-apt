"""경매 권리분석 — 등기부·임차인 입력 → 인수/소멸 판정 → 인수금액.

경매에서 실행을 막는 건 가격이 아니라 "떠안는 게 있나?"다. 그 금액이 나와야
입찰가 계산이 끝난다. 여기서 나온 인수합계는 `Listing.인수보증금` 으로 들어가
입찰가 산정표에 그대로 반영된다.

판정 규칙(주거용 아파트 기준):
  말소기준권리 = (근)저당권·압류·가압류·담보가등기·경매개시결정 중 **가장 빠른 것**.
  그보다 뒤 = 소멸, 앞 = 인수. 유치권·법정지상권은 등기 순서와 무관하게 인수 가능.
  임차인 대항력 = 전입신고 다음날 0시 → 전입일 < 말소기준일 이어야 대항력이 있다.

법률 자문이 아니다. 애매한 건 '확인필요'로 남기고 단정하지 않는다.
"""

from __future__ import annotations

from datetime import date

# 말소기준이 될 수 있는 권리 — 이 중 최선순위가 기준이 된다
BASELINE = ("근저당권", "저당권", "압류", "가압류", "담보가등기", "경매개시결정")
# 등기 순서와 무관하게 낙찰자가 떠안을 수 있는 것 — 현장·서류 확인이 필요
ALWAYS_CHECK = ("유치권", "법정지상권", "분묘기지권", "예고등기")
# 선순위면 인수, 후순위면 소멸
ORDER_DEPENDENT = ("지상권", "지역권", "전세권", "가등기", "가처분", "환매등기", "임차권등기")

_ALIAS = {
    "근저당": "근저당권", "저당": "저당권", "가압": "가압류",
    "소유권이전청구권가등기": "가등기", "소유권이전가등기": "가등기",
    "담보가등기권": "담보가등기", "강제경매개시결정": "경매개시결정",
    "임의경매개시결정": "경매개시결정", "경매기입등기": "경매개시결정",
    "전세권설정": "전세권", "지상권설정": "지상권", "가처분등기": "가처분",
}

# 소액임차인 최우선변제 참고선(2023-02-21 개정). 적용 표는 최선순위 담보물권
# 설정일에 따라 달라지므로 계산에 쓰지 않고 '확인필요' 플래그로만 쓴다. (만원)
SMALL_LEASE = {"서울": 16500, "과밀": 14500, "광역": 8500, "기타": 7500}

DISCLAIMER = ("자동 판정은 등기부 요약·임차인 입력만 본 참고치입니다. "
              "매각물건명세서·현황조사서·감정평가서를 반드시 직접 확인하세요.")


def _norm_kind(s: str) -> str:
    k = (s or "").strip().replace(" ", "")
    return _ALIAS.get(k, k)


def _d(s) -> date | None:
    if isinstance(s, date):
        return s
    t = str(s or "").strip().replace(".", "-").replace("/", "-")
    if len(t) == 8 and t.isdigit():
        t = f"{t[:4]}-{t[4:6]}-{t[6:]}"
    try:
        y, m, dd = (int(x) for x in t.split("-")[:3])
        return date(y, m, dd)
    except (ValueError, TypeError):
        return None


def _clean_rights(rows: list[dict] | None) -> list[dict]:
    out = []
    for r in rows or []:
        kind = _norm_kind(r.get("종류"))
        if not kind:
            continue
        out.append({
            "종류": kind, "일자": _d(r.get("일자")),
            "금액": float(r.get("금액") or 0), "권리자": (r.get("권리자") or "")[:60],
            "메모": (r.get("메모") or "")[:200],
        })
    out.sort(key=lambda r: r["일자"] or date.max)
    return out


def _clean_tenants(rows: list[dict] | None) -> list[dict]:
    out = []
    for t in rows or []:
        moved = _d(t.get("전입일"))
        deposit = float(t.get("보증금") or 0)
        if not moved and not deposit:
            continue
        out.append({
            "이름": (t.get("이름") or "")[:30], "전입일": moved,
            "확정일자": _d(t.get("확정일자")), "배당요구": bool(t.get("배당요구")),
            "보증금": deposit, "월세": float(t.get("월세") or 0),
            "점유": t.get("점유") is not False,
        })
    out.sort(key=lambda t: t["전입일"] or date.max)
    return out


def baseline_right(rights: list[dict]) -> dict | None:
    """말소기준권리 — 기준이 될 수 있는 권리 중 가장 빠른 것."""
    cands = [r for r in rights if r["종류"] in BASELINE and r["일자"]]
    return min(cands, key=lambda r: r["일자"]) if cands else None


def _judge_right(r: dict, base: dict | None) -> tuple[str, str]:
    kind = r["종류"]
    if kind in ALWAYS_CHECK:
        return "확인필요", f"{kind}은 등기 순서와 무관하게 인수될 수 있습니다. 성립 여부를 현장에서 확인하세요."
    if base is None:
        return "확인필요", "말소기준권리를 찾지 못했습니다. 등기부를 다시 입력해 주세요."
    if kind in BASELINE:
        return "소멸", "매각으로 소멸하는 권리입니다."
    if not r["일자"]:
        return "확인필요", "설정일이 없어 선후를 가릴 수 없습니다."
    if r["일자"] > base["일자"]:
        return "소멸", f"말소기준({base['종류']} {base['일자']}) 이후라 소멸합니다."
    if kind == "전세권":
        return "확인필요", ("선순위 전세권입니다. 배당요구를 했다면 소멸, 안 했다면 인수(보증금 전액)입니다. "
                        "매각물건명세서에서 배당요구 여부를 확인하세요.")
    if kind in ORDER_DEPENDENT:
        return "인수", f"말소기준({base['종류']} {base['일자']})보다 앞선 선순위라 인수합니다."
    return "확인필요", "분류되지 않은 권리입니다. 전문가 확인이 필요합니다."


def _judge_tenant(t: dict, base: dict | None) -> dict:
    """대항력 = 전입 다음날 0시 발생 → 전입일이 말소기준일보다 **앞서야** 한다."""
    out = dict(t)
    if base is None or not t["전입일"]:
        out.update({"대항력": None, "판정": "확인필요", "인수금액": 0,
                    "사유": "말소기준권리나 전입일이 없어 대항력을 판단할 수 없습니다."})
        return out
    has = t["전입일"] < base["일자"]
    out["대항력"] = has
    if not has:
        out.update({"판정": "소멸", "인수금액": 0,
                    "사유": f"전입 {t['전입일']}이 말소기준({base['일자']}) 이후라 대항력이 없습니다. "
                            "배당에서 못 받아도 낙찰자가 인수하지 않습니다."})
        return out
    if not t["배당요구"]:
        out.update({"판정": "인수", "인수금액": round(t["보증금"]),
                    "사유": f"대항력 있는 임차인이 배당요구를 하지 않았습니다. 보증금 전액을 인수합니다."})
        return out
    if not t["확정일자"]:
        out.update({"판정": "확인필요", "인수금액": round(t["보증금"]),
                    "사유": "대항력은 있으나 확정일자가 없어 우선변제를 못 받습니다. "
                            "배당 잔액만큼 인수하게 되며, 사실상 전액 인수로 보는 것이 안전합니다."})
        return out
    out.update({"판정": "확인필요", "인수금액": 0,
                "사유": "대항력·확정일자 모두 있어 배당순위에 따라 회수됩니다. "
                        "배당표상 미회수액이 있으면 그만큼 인수하니 예상배당액을 확인하세요."})
    return out


def analyze(권리: list[dict] | None, 임차인: list[dict] | None) -> dict:
    """등기부·임차인 → 인수/소멸 판정 + 인수합계 + 위험 플래그."""
    rights = _clean_rights(권리)
    tenants = _clean_tenants(임차인)
    base = baseline_right(rights)

    judged, assume = [], 0.0
    for r in rights:
        verdict, why = _judge_right(r, base)
        judged.append({**r, "일자": r["일자"].isoformat() if r["일자"] else None,
                       "판정": verdict, "사유": why})
    jt = []
    for t in tenants:
        j = _judge_tenant(t, base)
        assume += j["인수금액"]
        jt.append({**j,
                   "전입일": t["전입일"].isoformat() if t["전입일"] else None,
                   "확정일자": t["확정일자"].isoformat() if t["확정일자"] else None})

    risks = []
    if base is None:
        risks.append("말소기준권리 없음 — 등기부 입력이 불완전합니다. 이 상태로는 판정이 무의미합니다.")
    for r in judged:
        if r["종류"] in ALWAYS_CHECK:
            risks.append(f"{r['종류']} 신고 — 성립하면 낙찰자 부담. 금액이 크면 입찰 재고.")
        elif r["판정"] == "인수":
            risks.append(f"선순위 {r['종류']}({r['일자']}) 인수 — 말소되지 않습니다.")
        elif r["판정"] == "확인필요" and r["종류"] == "전세권":
            risks.append("선순위 전세권 — 배당요구 여부에 따라 전액 인수 가능.")
    for t in jt:
        if t["판정"] == "인수":
            risks.append(f"대항력 임차인 보증금 {round(t['보증금']):,}만 인수 — 배당요구 없음.")
        elif t["판정"] == "확인필요" and t.get("대항력"):
            risks.append(f"대항력 임차인({t.get('이름') or '성명미상'}) — 예상배당액 확인 필요.")

    grade = "안전"
    if assume > 0 or any(r["판정"] == "인수" for r in judged):
        grade = "위험"
    elif risks:
        grade = "주의"

    return {
        "말소기준": ({"종류": base["종류"], "일자": base["일자"].isoformat(),
                   "금액": round(base["금액"]) or None} if base else None),
        "권리": judged, "임차인": jt,
        "인수합계": round(assume),
        "위험": risks[:8], "등급": grade,
        "결론": _conclusion(base, assume, judged, jt, grade),
        "면책": DISCLAIMER,
    }


def _conclusion(base, assume: float, rights: list[dict], tenants: list[dict], grade: str) -> str:
    if base is None:
        return "등기부를 입력하면 인수/소멸을 판정합니다."
    head = f"말소기준은 {base['종류']}({base['일자'].isoformat()})입니다."
    if grade == "안전":
        return head + " 이후 권리는 모두 소멸하고, 인수할 보증금도 없습니다."
    bits = []
    if assume:
        bits.append(f"보증금 {round(assume):,}만 인수")
    n_take = sum(1 for r in rights if r["판정"] == "인수")
    if n_take:
        bits.append(f"선순위 권리 {n_take}건 인수")
    n_check = sum(1 for r in rights if r["판정"] == "확인필요") \
        + sum(1 for t in tenants if t["판정"] == "확인필요")
    if n_check:
        bits.append(f"확인필요 {n_check}건")
    return head + " " + ", ".join(bits) + ". 인수금액은 입찰가 계산에 자동 반영됩니다."


def small_lease_note(region: str, deposit: float) -> str | None:
    """소액임차인 최우선변제 가능성 안내 — 판정이 아니라 확인 유도."""
    if not deposit:
        return None
    if region.startswith("서울"):
        cap = SMALL_LEASE["서울"]
    elif any(k in region for k in ("인천", "성남", "부천", "고양", "안양", "과천", "의왕", "군포", "하남")):
        cap = SMALL_LEASE["과밀"]
    elif any(k in region for k in ("부산", "대구", "광주", "대전", "울산")):
        cap = SMALL_LEASE["광역"]
    else:
        cap = SMALL_LEASE["기타"]
    if deposit <= cap:
        return (f"보증금 {round(deposit):,}만은 소액임차인 기준({cap:,}만) 이하입니다. "
                "최우선변제 대상일 수 있으나, 적용 표는 최선순위 담보물권 설정일 기준이라 별도 확인이 필요합니다.")
    return None
