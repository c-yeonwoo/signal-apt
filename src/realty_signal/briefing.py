"""Nick 데일리 브리핑 — 확정 매수력 기준 후보 3곳의 '어제 대비 변화'만.

매일 같은 요약을 보내면 읽지 않게 된다. 변화가 없는 날은 보내지 않고,
월요일 한 번은 변화가 없어도 후보 현황을 확인용으로 보낸다.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from realty_signal import buying_power, config, db

log = logging.getLogger("realty_signal.briefing")

KST = timezone(timedelta(hours=9))
SNAP_KEY = "briefing_snap:{uid}"
MOVE_MIN = 100          # 예상가 변동 표시 최소폭(만원)
QUICKSALE_FILE = Path("data/cache/quicksale.json")

SIG_LABEL = {"STRONG_BUY": "적극매수", "BUY": "매수", "WATCH": "관망",
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


def _quicksales(regions: set[str], budget: float) -> list[dict]:
    """예산 안에 들어오는 급매만. 후보·관심지역으로 좁힌다."""
    if not QUICKSALE_FILE.exists():
        return []
    try:
        rows = json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", [])
    except Exception:  # noqa: BLE001
        return []
    out = [m for m in rows
           if m.get("지역") in regions and (m.get("호가") or 0) and m["호가"] <= budget]
    out.sort(key=lambda m: m.get("급매갭") if m.get("급매갭") is not None else 0)
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
        if old and old != sig:
            out.append({"region": region, "from": old, "to": sig,
                        "up": SIG_RANK.get(sig, 0) > SIG_RANK.get(old, 0)})
    return out


def _todo(diff: dict, sigs: list[dict], qs: list[dict], cands: list[dict],
          visited: dict | None = None, auctions: list[dict] | None = None) -> str:
    urgent = next((a for a in (auctions or []) if a["D"] <= 1), None)
    if urgent:      # 기일은 미룰 수 없다 — 무조건 먼저
        if urgent["kind"] == "bid":
            return f"{urgent['단지']} 입찰가 확정·보증금 준비 ({urgent['날짜']})"
        return f"{urgent['단지']} {urgent['단계']} — {urgent['할일']}"
    if diff["new"]:
        c = diff["new"][0]
        return f"{c['단지']}({c['region']}) 실거래·평면 확인"
    ups = [s for s in sigs if s["up"]]
    if ups:
        return f"{ups[0]['region']} 동네 리포트 다시 보기"
    if qs:
        return f"{qs[0].get('지역')} 급매 {len(qs)}건 확인"
    if cands:
        seen = visited or {}
        todo = [c for c in cands if f"{c['region']}|{c['단지']}" not in seen]
        if todo:
            return f"{todo[0]['단지']} 임장 잡기 (앱에서 코스 생성)"
        return "임장 기록 비교해서 후보 1곳으로 좁히기"
    return "마이페이지에 직장 주소를 넣어 통근 필터 켜기"


def build(uid: int, *, force: bool = False) -> dict:
    """유저 1인 브리핑. 보낼 게 없으면 {'send': False, 'reason': ...}."""
    from realty_signal.services import shortlist as sl

    profile = dict(db.profile_get(uid) or {})
    profile["_favs"] = [f["key"] for f in db.fav_list(uid) if f["kind"] == "region"]
    budget = (profile.get("매수력") or {}).get("최대매수가")
    if not budget:
        p = buying_power.params_from_profile(profile)
        budget = buying_power.max_purchase(p)[0] if p.capital > 0 else 0
    if not budget:
        return {"send": False, "reason": "no_budget"}

    data = sl.build(profile, float(budget), limit=3)
    cands = data.get("candidates") or []
    from realty_signal import api as app_api
    signal_map = app_api._signal_map()
    watch = set(profile["_favs"]) | {c["region"] for c in cands}
    cur_sigs = {r: signal_map[r] for r in watch if r in signal_map}

    prev = db.kv_get(SNAP_KEY.format(uid=uid)) or {}
    first = not prev
    diff = _diff_candidates(cands, prev.get("candidates") or {})
    sigs = _diff_signals(cur_sigs, prev.get("signals") or {})
    qs = _quicksales(watch, float(budget))
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
                   visited=db.imjang_latest(uid), auctions=auctions)
    return {"send": True, "text": text, "snapshot": snapshot, "news": news,
            "first": first, "candidates": cands}


def _render(profile: dict, data: dict, diff: dict, sigs: list[dict],
            qs: list[dict], qs_new: int, *, first: bool,
            visited: dict | None = None, auctions: list[dict] | None = None) -> str:
    d = today_kst()
    cands = data.get("candidates") or []
    L = [f"🦊 Nick 브리핑 · {d.month}/{d.day}({WEEKDAY_KO[d.weekday()]})", ""]
    L.append(f"예산 {_eok(data.get('budget'))} · {data.get('pyeong')}평 기준")
    L.append("")

    if first:
        L.append("[이번 주 볼 단지]")
        for c in cands:
            L.append(f"· {c['단지']} ({c['region']}) {_eok(c.get('예상가'))} — {c.get('근거', '')}")
        if not cands:
            L.append("· 예산 안에 드는 후보가 없어요. 매수력이나 관심지역을 조정해 보세요.")
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
        head = f"[예산 내 급매] {len(qs)}건" + (f" (신규 {qs_new})" if qs_new > 0 else "")
        L.append(head)
        for m in qs[:2]:
            gap = m.get("급매갭")
            gap_s = f" (시세 {gap:+.1f}%)" if gap is not None else ""
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

    L.append(f"오늘 할 일 → {_todo(diff, sigs, qs, cands, visited, auctions)}")
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
            if snap:               # 발송 성공 후에만 기준점 이동(실패 시 변화 유실 방지)
                db.kv_set(SNAP_KEY.format(uid=u["id"]), snap)
        else:
            stats["errors"] += 1
    return stats
