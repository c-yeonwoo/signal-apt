"""복귀 브리핑 — 자리를 비운 동안 놓친 것을 돌려준다.

왜 필요한가 (2026-09-06 조사):
    홈의 '이번 주 변화'는 **직전 1주만** 비교한다. 5주 자리를 비운 사용자가 돌아오면
    그 사이의 변화가 구조적으로 유실된다. 실제로 관심지역 강남구가
    STRONG_BUY → BUY(8/10) → WATCH(8/31) 로 두 단계 내려갔는데, 8/10 의 첫 강등은
    영영 화면에 뜨지 않았다.

데이터 원천은 `signal_changes` kv 로그다(최대 300건, `{region, from, to, date}`).
과거 주차마다 엔진을 다시 돌리지 않아도 경로를 그대로 복원할 수 있다.

스냅샷 규칙 — 이게 중요하다:
    · 텔레그램 브리핑(`briefing_snap:{uid}`)과 **다른 키를 쓴다.** 같이 쓰면 브리핑이
      발송될 때마다 복귀 브리핑이 사라진다(CLAUDE.md 의 기존 교훈과 같은 함정).
    · 기준점은 **보여준 뒤에** 옮긴다. 계산에 실패했는데 옮기면 변화를 통째로 잃는다.
"""

from __future__ import annotations

from realty_signal import db

KV_PREFIX = "last_visit:"

# 1주 차이면 '이번 주 변화' 카드가 이미 같은 내용을 보여준다 — 중복해서 말하지 않는다.
MIN_WEEKS = 2

_RANK = {"SELL_RISK": 0, "NEUTRAL": 1, "WATCH": 2, "BUY": 3, "STRONG_BUY": 4}
_KO = {"STRONG_BUY": "강력매수", "BUY": "매수", "WATCH": "관망",
       "NEUTRAL": "중립", "SELL_RISK": "매도주의"}


def buyer_note(frm: str, to: str) -> dict | None:
    """등급 변화를 **실거주 매수 대기자 관점**으로 옮긴다.

    ⚠️ 용어 주의: KB '매수우위지수' 는 매수 *문의가 많다* 는 뜻이라 높을수록 시장이 뜨겁다.
    그래서 등급 하향을 "매수자 우위로 이동" 이라고 쓰면 앱 자신의 지표와 반대로 읽힌다.
    여기서는 **매수 압력의 방향**과 **서두를 이유**로만 말한다.

    확신형 예측은 쓰지 않는다("지금이 기회" 금지). 방향과 그 의미까지만.
    """
    a, b = _RANK.get(frm), _RANK.get(to)
    if a is None or b is None or a == b:
        return None
    if b < a:
        return {
            "dir": "cool",
            "label": "서두를 이유 감소",
            "why": "매수 신호가 약해졌습니다. 급하게 붙지 않아도 되는 구간으로 이동 중입니다.",
        }
    return {
        "dir": "heat",
        "label": "조건 강화",
        "why": "매수 신호가 강해졌습니다. 관심 단지가 있으면 선택지가 줄기 전에 확인해 보세요.",
    }


def _weeks_between(dates: list[str], frm: str, to: str) -> int:
    """KB 주차 기준 경과 주 수. 달력 일수가 아니라 **발표 주차**로 센다."""
    return sum(1 for d in dates if frm < d <= to)


def _paths(changes: list[dict]) -> dict[str, dict]:
    """지역별로 변화를 날짜순으로 이어 `from → … → to` 경로를 만든다."""
    by_region: dict[str, list[dict]] = {}
    for c in changes:
        r = c.get("region")
        if r and c.get("from") and c.get("to"):
            by_region.setdefault(r, []).append(c)
    out = {}
    for r, cs in by_region.items():
        cs.sort(key=lambda x: x.get("date") or "")
        frm, to = cs[0]["from"], cs[-1]["to"]
        if frm == to:            # 갔다가 돌아온 경우 — 순변화 없음
            continue
        out[r] = {
            "region": r, "from": frm, "to": to,
            "from_ko": _KO.get(frm, frm), "to_ko": _KO.get(to, to),
            "up": _RANK.get(to, 1) > _RANK.get(frm, 1),
            "steps": len(cs),
            "path": [{"date": c["date"], "from": c["from"], "to": c["to"],
                      "from_ko": _KO.get(c["from"], c["from"]),
                      "to_ko": _KO.get(c["to"], c["to"])} for c in cs],
            "buyer": buyer_note(frm, to),
        }
    return out


def compute(uid: int, favs: set[str], as_of: str, kb_dates: list[str]) -> dict:
    """복귀 브리핑. 준비 안 됐으면 `ready=False` + 사유를 준다.

    0 을 이유 없이 비우지 않는다 — '첫 방문'·'최근 방문'·'조용했음' 은 다른 상태다.
    """
    prev = db.kv_get(KV_PREFIX + str(uid)) or {}
    prev_as_of = prev.get("as_of")
    base = {"ready": False, "as_of": as_of, "prev_as_of": prev_as_of}

    if not prev_as_of:
        return {**base, "reason": "first_visit"}
    if prev_as_of >= as_of:
        return {**base, "reason": "no_new_week", "weeks": 0}

    weeks = _weeks_between(kb_dates, prev_as_of, as_of)
    if weeks < MIN_WEEKS:
        return {**base, "reason": "recent_visit", "weeks": weeks}

    log = db.kv_get("signal_changes") or []
    rel = [c for c in log if prev_as_of < (c.get("date") or "") <= as_of]
    paths = _paths(rel)

    mine = [v for r, v in paths.items() if r in favs]
    rest = [v for r, v in paths.items() if r not in favs]
    mine.sort(key=lambda x: (x["up"], -x["steps"]))
    rest.sort(key=lambda x: (x["up"], -x["steps"]))

    return {
        "ready": True, "as_of": as_of, "prev_as_of": prev_as_of, "weeks": weeks,
        "mine": mine, "rest": rest[:12], "rest_total": len(rest),
        "totals": {
            "regions": len(paths),
            "down": sum(1 for v in paths.values() if not v["up"]),
            "up": sum(1 for v in paths.values() if v["up"]),
        },
        # 변화가 0 이어도 ready=True 다 — '조용한 5주' 도 알려줄 정보다
        "quiet": not paths,
    }


def mark_seen(uid: int, as_of: str) -> None:
    """기준점을 앞으로 당긴다. **브리핑을 만들어 보낸 뒤에만** 부른다."""
    db.kv_set(KV_PREFIX + str(uid), {"as_of": as_of})
