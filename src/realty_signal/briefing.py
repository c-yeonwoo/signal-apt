"""Signal APT 아침 요약 — 확정 매수력 기준 후보 3곳의 '어제 대비 변화'만.

매일 같은 요약을 보내면 읽지 않게 된다. 변화가 없는 날은 보내지 않고,
월요일 한 번은 변화가 없어도 후보 현황을 확인용으로 보낸다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from realty_signal import buying_power, config, db

log = logging.getLogger("realty_signal.briefing")

KST = timezone(timedelta(hours=9))
SNAP_KEY = "briefing_snap:{uid}"
MOVE_MIN = 100          # 예상가 변동 표시 최소폭(만원)
QUICKSALE_FILE = Path("data/cache/quicksale.json")

SIG_LABEL = {"STRONG_BUY": "강력매수", "BUY": "매수", "WATCH": "관망",
             "NEUTRAL": "중립", "SELL_RISK": "매도주의"}
SIG_RANK = {"SELL_RISK": 0, "NEUTRAL": 1, "WATCH": 2, "BUY": 3, "STRONG_BUY": 4}
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


def _eok(man: float | None) -> str:
    if not man:
        return "–"
    return f"{man / 10000:.2f}억".replace(".00억", "억")


def _key(c: dict) -> str:
    return f"{c.get('region')}|{c.get('단지')}"


def _quicksales(regions: set[str], budget: float, *, uid: int | None = None) -> list[dict]:
    """예산 안에 들어오는 급매만. 후보·관심지역으로 좁힌다."""
    if not config.personal_listing_allowed(db.user_email(uid)):
        return []
    if not QUICKSALE_FILE.exists():
        return []
    try:
        from realty_signal import api
        rows = api._radar_verified_rows(QUICKSALE_FILE, api._QUICKSALE_SCAN_VER)
    except Exception:  # noqa: BLE001
        return []
    out = [m for m in rows
           if m.get("지역") in regions and api._listing_region_matches_kb(m.get("지역"), m.get("시도"), m.get("지역코드"))
           and (m.get("호가") or 0) and m["호가"] <= budget]
    return out


AUCTION_WINDOW = 7      # 입찰기일 며칠 전부터 브리핑에 올릴지
ALERT_DDAYS = (7, 3, 1, 0)   # 이 날짜에만 '새 소식'으로 센다(매일 카운트되지 않게)


def _auction_alerts() -> list[dict]:
    """입찰기일이 임박한 매물 + 낙찰 후 다가온 단계. 등록 매물은 내가 고른 것이라 전부 본다."""
    from realty_signal import auction

    today = today_kst()
    out = []
    try:
        listings = auction.load()
    except Exception:  # noqa: BLE001
        return []
    for lst in listings:
        d = auction._date_of(lst.입찰기일)
        if d:
            dday = (d - today).days
            if 0 <= dday <= AUCTION_WINDOW:
                out.append({"kind": "bid", "D": dday, "단지": lst.단지명,
                            "region": lst.region, "사건": lst.사건번호,
                            "최저": lst.최저매각가, "날짜": d.isoformat()})
        if not lst.낙찰일:
            continue
        try:
            for s in auction.plan(lst)["steps"]:
                sd = date.fromisoformat(s["날짜"])
                if 0 <= (sd - today).days <= AUCTION_WINDOW:
                    out.append({"kind": "plan", "D": (sd - today).days, "단지": lst.단지명,
                                "region": lst.region,
                                "단계": s["단계"], "할일": s["할일"], "금액": s["금액"],
                                "날짜": s["날짜"]})
                    break
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda a: a["D"])
    return out


def _diff_candidates(cur: list[dict], prev: dict) -> dict:
    """전일 스냅샷 대비 신규·이탈·가격변동."""
    cur_map = {_key(c): c for c in cur}
    prev_map = prev or {}
    new = [c for k, c in cur_map.items() if k not in prev_map]
    dropped = [k for k in prev_map if k not in cur_map]
    moved = []
    for k, c in cur_map.items():
        old = prev_map.get(k)
        if old is None:
            continue
        delta = (c.get("예상가") or 0) - old
        if abs(delta) >= MOVE_MIN:
            moved.append({**c, "delta": delta})
    return {"new": new, "dropped": dropped, "moved": moved}


def _diff_signals(cur: dict, prev: dict) -> list[dict]:
    out = []
    for region, sig in cur.items():
        old = (prev or {}).get(region)
        # HELD는 새 매수/매도 등급도, 복구 후 승급의 출발 등급도 아니다.
        if old in SIG_RANK and sig in SIG_RANK and old != sig:
            out.append({"region": region, "from": old, "to": sig,
                        "up": SIG_RANK.get(sig, 0) > SIG_RANK.get(old, 0)})
    return out


def _act(key: str, title: str, why: str, tab: str, cta: str, *, urgent: bool = False,
         region: str | None = None) -> dict:
    action = {"key": key, "title": title, "why": why, "tab": tab, "cta": cta, "urgent": urgent}
    if region:
        action["region"] = region
    return action


def actions(diff: dict, sigs: list[dict], qs: list[dict], cands: list[dict],
            visited: dict | None = None, auctions: list[dict] | None = None,
            *, profile: dict | None = None, confirmed: bool = True,
            budget: float = 0, asks: list[dict] | None = None) -> list[dict]:
    """다음에 할 일 — 우선순위 순. 텔레그램 '오늘 할 일'과 홈 카드가 같은 판단을 쓴다.

    두 화면이 각자 판단하면 앱이 서로 다른 말을 하게 된다. 순서는 **되돌릴 수 없는 것**부터다:
    입찰기일 > 돈(예산·확정) > 상한 안 확인된 호가 > 이번 주 바뀐 것 > 설정 보완.
    호가가 없으면 같은 돈의 네 갈래가 그 자리다. 급매 건수는 첫 문장이 아니다.
    """
    p = profile or {}
    out: list[dict] = []

    for a in (auctions or []):
        if a["D"] > 1:
            continue
        when = "오늘" if a["D"] == 0 else f"D-{a['D']}"
        if a["kind"] == "bid":
            out.append(_act("auction_bid", f"{a['단지']} 입찰가 확정·보증금 준비",
                            f"입찰기일 {when}({a['날짜']}) — 미루면 끝입니다", "auction",
                            "입찰가 산정표 열기", urgent=True, region=a.get("region")))
        else:
            out.append(_act("auction_step", f"{a['단지']} {a['단계']}",
                            f"{when} — {a['할일']}", "auction", "낙찰 후 플랜 열기", urgent=True,
                            region=a.get("region")))

    if not budget:
        out.append(_act("no_budget", "가용자본 입력하기",
                        "예산을 모르면 추천도 후보도 만들 수 없습니다", "mypage", "마이페이지 →"))
    elif not confirmed:
        out.append(_act("confirm_power", "매수력 확정하기",
                        "확정해 두면 후보·급매·브리핑이 모두 같은 예산을 씁니다",
                        "dashboard", "매수력 카드에서 확정 →"))
    elif asks:
        top = asks[0]
        price = top.get("총액") or top.get("호가") or top.get("예상가")
        out.append(_act("asks", f"{top.get('단지명') or '매물'} 매매가 {_eok(price)}",
                        f"예산 안에서 가격이 있는 매물 {len(asks)}곳입니다. 동네 평균으로 짐작한 단지가 아닙니다.",
                        "all", "매물 목록에서 보기", region=top.get("지역")))
    else:
        out.append(_act("levers", "같은 돈으로 사는 방법 보기",
                        "예산 안에 맞는 매물이 없습니다. 실거주, 갭, 경매, 재건축을 나란히 봅니다.",
                        "dashboard", "방법 보기"))

    for s in [x for x in sigs if x["up"]][:2]:
        out.append(_act("signal_up", f"{s['region']} 동네 리포트 다시 보기",
                        f"이번 주 {SIG_LABEL.get(s['from'], s['from'])} → "
                        f"{SIG_LABEL.get(s['to'], s['to'])}로 올라섰습니다", "signal", "동네 리포트 →",
                        region=s["region"]))
    for s in [x for x in sigs if not x["up"]][:1]:
        out.append(_act("signal_down", f"{s['region']} 후보 재검토",
                        f"이번 주 {SIG_LABEL.get(s['from'], s['from'])} → "
                        f"{SIG_LABEL.get(s['to'], s['to'])}로 내려갔습니다", "signal", "시그널 보기 →",
                        region=s["region"]))

    for c in (diff.get("new") or [])[:2]:
        out.append(_act("new_candidate", f"{c['단지']}({c['region']}) 실거래·평면 확인",
                        "이번 주 새로 후보에 들어온 단지입니다", "dashboard", "단지 상세 →",
                        region=c.get("region")))

    if cands:
        seen = visited or {}
        todo = [c for c in cands if f"{c['region']}|{c['단지']}" not in seen]
        if todo:
            out.append(_act("imjang", f"{todo[0]['단지']} 임장 잡기",
                            "현장에서만 알 수 있는 것들이 있습니다 — 코스를 짜 드립니다",
                            "dashboard", "임장 코스 짜기 →", region=todo[0].get("region")))
        else:
            out.append(_act("imjang_compare", "임장 기록 비교해서 1곳으로 좁히기",
                            "후보를 다 봤습니다 — 점수를 나란히 놓고 고를 차례입니다",
                            "dashboard", "임장 기록 →", region=cands[0].get("region")))
    elif not p.get("_favs"):
        # ★가 있는데 후보만 없는 경우는 예산 문제라 위에서 이미 말했다 — 두 번 시키지 않는다
        out.append(_act("no_favorite", "관심 지역 ★ 추가하기",
                        "★가 있어야 주간 변화와 후보 추천이 내 것으로 좁혀집니다",
                        "signal", "시그널 지도 →"))

    if not p.get("직장"):
        out.append(_act("no_job", "직장 주소 넣기",
                        "통근 시간 필터가 꺼져 있어 후보가 넓게 잡힙니다", "mypage", "마이페이지 →"))
    return out


def _todo(diff: dict, sigs: list[dict], qs: list[dict], cands: list[dict],
          visited: dict | None = None, auctions: list[dict] | None = None,
          profile: dict | None = None, budget: float = 0,
          asks: list[dict] | None = None) -> str:
    """텔레그램 한 줄 — 액션 목록의 첫 항목. 판단은 `actions()` 한 곳에서만 한다."""
    acts = actions(diff, sigs, qs, cands, visited, auctions, profile=profile,
                   budget=budget, asks=asks)
    return acts[0]["title"] if acts else "마이페이지에 직장 주소를 넣어 통근 필터 켜기"


def asks_within(uid: int, profile: dict, budget: float, *, limit: int = 3) -> list[dict]:
    """홈과 텔레그램이 같은 호가 3곳을 보게 한다."""
    if not budget:
        return []
    from realty_signal import api as app_api
    from realty_signal.services.asks import known_asks

    allowed = bool(uid) and config.personal_listing_allowed(db.user_email(uid))
    rows = app_api._build_listings({"급매", "찐매물", "일반매물"}, include_private=allowed)
    params = buying_power.params_from_profile(profile)
    return known_asks(rows, params, float(budget), uid=uid, sido_of=app_api._sido_of, limit=limit)


def _safe_weekly_changes(weekly_result: dict) -> list[dict]:
    """지난 KB 변화는 현재 유효한 동일 등급일 때만 행동 카드가 된다."""
    stale_days = weekly_result.get("stale_days")
    if (not weekly_result.get("ready") or type(stale_days) not in (int, float)
            or not 0 <= stale_days <= 8):
        return []
    from realty_signal import api as app_api
    try:
        current = app_api._display_signal_map()
    except Exception:  # noqa: BLE001 - 현재 판정을 확인할 수 없으면 원시 변화로 대체하지 않는다.
        return []
    return [row for row in weekly_result.get("mine") or []
            if row.get("to") in {"STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "SELL_RISK"}
            and current.get(row.get("region")) == row.get("to")]


def plan(uid: int) -> dict:
    """홈 '다음 할 일'. 브리핑과 같은 `actions()` 를 쓰되, 변화 기준은 **이번 주 KB 갱신**이다.

    브리핑은 '어제 대비'라 매일 기준점이 움직이지만 홈은 주간 화면이다. 두 화면이
    같은 스냅샷을 공유하면 브리핑이 발송될 때마다 홈의 '이번 주 변화'가 사라진다.
    """
    from realty_signal import weekly
    from realty_signal.services import shortlist as sl

    profile = dict(db.profile_get(uid) or {})
    profile["_favs"] = db.actionable_region_favs(uid)
    confirmed = buying_power.validated_confirmed_power(profile) is not None
    p = buying_power.params_from_profile(profile)
    budget = buying_power.max_purchase(p)[0] if p.capital > 0 else 0

    cands: list[dict] = []
    qs: list[dict] = []
    watch = set(profile["_favs"])
    if budget:
        try:
            cands = (sl.build(profile, float(budget), limit=3, budget_is_ceiling=False) or {}).get("candidates") or []
        except Exception as e:  # noqa: BLE001 — 후보가 없어도 나머지 할 일은 나와야 한다
            log.warning("액션플랜 숏리스트 실패 uid=%s: %s", uid, e)
        watch |= {c["region"] for c in cands}
        qs = _quicksales(watch, float(budget), uid=uid)

    asks = asks_within(uid, profile, float(budget or 0))
    wk = weekly.for_user(watch)
    prev = db.kv_get(SNAP_KEY.format(uid=uid)) or {}
    diff = _diff_candidates(cands, prev.get("candidates") or {}) if prev else {"new": [], "dropped": [], "moved": []}
    acts = actions(diff, _safe_weekly_changes(wk), qs, cands, db.imjang_latest(uid), _auction_alerts(),
                   profile=profile, confirmed=confirmed, budget=float(budget or 0), asks=asks)
    return {"actions": acts, "budget": round(float(budget)) if budget else 0,
            "confirmed": confirmed, "candidates": len(cands), "asks": len(asks),
            "watching": sorted(watch), "as_of": wk.get("as_of")}


def build(uid: int, *, force: bool = False) -> dict:
    """유저 1인 브리핑. 보낼 게 없으면 {'send': False, 'reason': ...}."""
    from realty_signal.services import shortlist as sl

    profile = dict(db.profile_get(uid) or {})
    profile["_favs"] = db.actionable_region_favs(uid)
    p = buying_power.params_from_profile(profile)
    budget = buying_power.max_purchase(p)[0] if p.capital > 0 else 0
    data = (sl.build(profile, float(budget), limit=3, budget_is_ceiling=False) if budget else
            {"budget": 0, "pyeong": None, "candidates": [], "직장": profile.get("직장")})
    cands = data.get("candidates") or []
    from realty_signal import api as app_api
    signal_map = app_api._display_signal_map()
    watch = set(profile["_favs"]) | {c["region"] for c in cands}
    cur_sigs = {r: signal_map.get(r, "HELD") for r in watch}

    prev = db.kv_get(SNAP_KEY.format(uid=uid)) or {}
    first = not prev
    diff = _diff_candidates(cands, prev.get("candidates") or {})
    sigs = _diff_signals(cur_sigs, prev.get("signals") or {})
    qs = _quicksales(watch, float(budget), uid=uid)
    asks = asks_within(uid, profile, float(budget))
    qs_new = len(qs) - int(prev.get("quicksale") or 0)
    auctions = _auction_alerts()

    snapshot = {
        "date": today_kst().isoformat(),
        "budget": round(float(budget)),
        "candidates": {_key(c): c.get("예상가") for c in cands},
        "signals": cur_sigs,
        "quicksale": len(qs),
    }
    news = len(diff["new"]) + len(diff["dropped"]) + len(diff["moved"]) + len(sigs) \
        + (qs_new if qs_new > 0 else 0) \
        + sum(1 for a in auctions if a["D"] in ALERT_DDAYS)
    weekly = today_kst().weekday() == 0
    if not first and not news and not weekly and not force:
        return {"send": False, "reason": "no_news", "snapshot": snapshot}

    text = _render(profile, data, diff, sigs, qs, qs_new, first=first,
                   visited=db.imjang_latest(uid), auctions=auctions,
                   asks=asks)
    return {"send": True, "text": text, "snapshot": snapshot, "news": news,
            "first": first, "candidates": cands}


def _render(profile: dict, data: dict, diff: dict, sigs: list[dict],
            qs: list[dict], qs_new: int, *, first: bool,
            visited: dict | None = None, auctions: list[dict] | None = None,
            asks: list[dict] | None = None) -> str:
    d = today_kst()
    cands = data.get("candidates") or []
    L = [f"📍 Signal APT 아침 요약 · {d.month}/{d.day}({WEEKDAY_KO[d.weekday()]})", ""]
    L.append((f"예산 {_eok(data.get('budget'))} · {data.get('pyeong')}평 기준"
              if data.get("budget") else "매수력 미설정 · 관심지역 변화와 일정 중심"))
    L.append("")

    if first:
        L.append("[이번 주 볼 단지]" if data.get("budget") else "[시작하기]")
        for c in cands:
            lines = c.get("lines") or {}
            tail = f" | {lines['cash']} | {lines.get('unknown', '')}" if lines.get("cash") else ""
            L.append(f"· {c['단지']} ({c['region']}) {_eok(c.get('예상가'))} — {c.get('근거', '')}{tail}")
        if not cands:
            L.append("· 매수력을 입력하면 예산에 맞는 후보를 볼 수 있어요." if not data.get("budget")
                     else "· 예산 안에 드는 후보가 없어요. 매수력이나 관심지역을 조정해 보세요.")
        L.append("")
    else:
        lines = []
        for c in diff["new"]:
            lines.append(f"+ {c['단지']} ({c['region']}) {_eok(c.get('예상가'))} — 새로 진입")
        for k in diff["dropped"]:
            region, _, name = k.partition("|")
            lines.append(f"- {name} ({region}) — 후보에서 이탈")
        for c in diff["moved"]:
            arrow = "▲" if c["delta"] > 0 else "▼"
            lines.append(f"· {c['단지']} ({c['region']}) {_eok(c.get('예상가'))} "
                         f"({arrow}{abs(c['delta']):,}만)")
        if lines:
            L.append("[후보 변화]")
            L.extend(lines)
            L.append("")
        elif cands:
            L.append("[후보 유지] " + ", ".join(f"{c['단지']}({c['region']})" for c in cands))
            L.append("")

    if sigs:
        L.append("[관심지역 시그널]")
        for s in sigs:
            mark = "↑" if s["up"] else "↓"
            L.append(f"{mark} {s['region']}: {SIG_LABEL.get(s['from'], s['from'])}"
                     f" → {SIG_LABEL.get(s['to'], s['to'])}")
        L.append("")

    if qs and (first or qs_new > 0):
        head = f"[예산 내 공급사 급매 표시] {len(qs)}건" + (f" (신규 {qs_new})" if qs_new > 0 else "")
        L.append(head)
        for m in qs[:2]:
            gap_s = " (동일 면적·조건 가격 비교 필요)"
            py = f"{m['평형']}평 " if m.get("평형") else ""
            L.append(f"· {m.get('단지명')} {py}{_eok(m.get('호가'))}{gap_s}")
        L.append("")

    if auctions:
        L.append("[경매]")
        for a in auctions[:4]:
            dd = "오늘" if a["D"] == 0 else f"D-{a['D']}"
            if a["kind"] == "bid":
                low = f" · 최저 {_eok(a['최저'])}" if a.get("최저") else ""
                L.append(f"· {dd} 입찰 — {a['단지']}({a['region']}) {a.get('사건') or ''}{low}")
            else:
                amt = f" · {abs(a['금액']):,}만" if a.get("금액") else ""
                L.append(f"· {dd} {a['단계']} — {a['단지']}{amt}: {a['할일']}")
        L.append("")

    if not data.get("직장"):
        L.append("※ 직장 주소가 없어 통근 필터가 꺼져 있습니다. 마이페이지에서 넣어 주세요.")
        L.append("")

    L.append(f"오늘 할 일 → {_todo(diff, sigs, qs, cands, visited, auctions, profile, data.get('budget') or 0, asks)}")
    L.append(f"{config.app_base_url()}/#dashboard")
    L.append("")
    L.append("끄기: /stop")
    return "\n".join(L)


def run(*, send: bool = True, quiet: bool = False, force: bool = False) -> dict:
    """텔레그램 연결 유저 전원 브리핑. 반환: 발송 통계."""
    from realty_signal import telegram

    users = db.users_with_telegram()
    stats = {"total": len(users), "sent": 0, "skipped": 0, "errors": 0, "dry_run": 0}
    for u in users:
        sent_key = f"briefing_sent:{today_kst().isoformat()}:{u['id']}"
        if send and not force and db.kv_get(sent_key):
            stats["skipped"] += 1
            continue
        try:
            b = build(u["id"], force=force)
        except Exception as e:  # noqa: BLE001
            log.error("브리핑 생성 실패 uid=%s: %s", u["id"], e)
            stats["errors"] += 1
            continue
        snap = b.get("snapshot")
        if not b.get("send"):
            if send and snap:      # 변화 없음도 기준점은 갱신
                db.kv_set(SNAP_KEY.format(uid=u["id"]), snap)
            stats["skipped"] += 1
            continue
        if not send:               # dry-run 은 스냅샷을 건드리지 않는다
            stats["dry_run"] += 1
            if not quiet:
                print(f"--- {u['email']} ---\n{b['text']}\n")
            continue
        if telegram.send_message(u["chat_id"], b["text"]):
            stats["sent"] += 1
            db.kv_set(sent_key, {"sent": True})
            db.kv_set(f"telegram_last_sent:{u['id']}", {"kind": "briefing"})
            if snap:               # 발송 성공 후에만 기준점 이동(실패 시 변화 유실 방지)
                db.kv_set(SNAP_KEY.format(uid=u["id"]), snap)
        else:
            stats["errors"] += 1
    return stats
