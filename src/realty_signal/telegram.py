"""텔레그램 봇 — Nick 데일리 브리핑 발송 채널.

웹훅 대신 getUpdates 폴링으로 계정을 연결한다. 배포/로컬에서 웹훅 등록·해제를
신경 쓸 필요가 없고, 연결 이벤트는 하루 몇 건이라 폴링으로 충분하다.

연결 흐름: 앱에서 일회용 코드 발급 → t.me/<bot>?start=<code> 딥링크 →
유저가 봇에 /start <code> 전송 → 서버 폴링이 코드↔uid 를 맞춰 chat_id 저장.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import urllib.request

from realty_signal import db

log = logging.getLogger("realty_signal.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
LINK_TTL = 600          # 일회용 연결 코드 유효시간(초)
MAX_LEN = 3900          # 텔레그램 메시지 상한(4096) 여유분
OFFSET_KEY = "tg_offset"


def token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def available() -> bool:
    return bool(token())


def _call(method: str, payload: dict | None = None, timeout: int = 15) -> dict | None:
    tk = token()
    if not tk:
        return None
    url = API.format(token=tk, method=method)
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())  # noqa: S310
    except Exception as e:  # noqa: BLE001
        log.warning("telegram %s 실패: %s", method, e)
        return None
    return r if r.get("ok") else None


def bot_username() -> str | None:
    """봇 핸들 — 딥링크 생성용. 1일 캐시."""
    cached = db.kv_get("tg_bot_username", max_age=86400)
    if cached:
        return cached
    r = _call("getMe")
    name = ((r or {}).get("result") or {}).get("username")
    if name:
        db.kv_set("tg_bot_username", name)
    return name


def send_message(chat_id: int | str, text: str) -> bool:
    """평문 발송. 마크다운을 쓰지 않아 지역·단지명 특수문자 이스케이프 문제가 없다."""
    if not chat_id:
        return False
    body = text if len(text) <= MAX_LEN else text[:MAX_LEN] + "\n…(생략)"
    r = _call("sendMessage", {"chat_id": chat_id, "text": body,
                              "disable_web_page_preview": True})
    return bool(r)


# ---------- 계정 연결 ----------
def chat_id_of(profile: dict | None) -> int | None:
    tg = (profile or {}).get("telegram") or {}
    cid = tg.get("chat_id")
    return cid if cid else None


def issue_link_code(uid: int) -> dict:
    """일회용 코드 + 딥링크. 코드는 kv 에 uid 로 매핑되고 LINK_TTL 후 만료."""
    code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
    db.kv_set(f"tg_link:{code}", uid)
    bot = bot_username()
    return {
        "code": code,
        "bot": bot,
        "url": f"https://t.me/{bot}?start={code}" if bot else None,
        "expires_in": LINK_TTL,
    }


def _attach(uid: int, chat_id: int, msg: dict) -> None:
    profile = db.profile_get(uid) or {}
    frm = msg.get("from") or {}
    profile["telegram"] = {
        "chat_id": chat_id,
        "username": frm.get("username"),
        "linked_at": int(time.time()),
    }
    db.profile_set(uid, profile)


def unlink(uid: int) -> bool:
    profile = db.profile_get(uid) or {}
    if not profile.get("telegram"):
        return False
    profile.pop("telegram", None)
    db.profile_set(uid, profile)
    return True


def poll_updates(limit: int = 50) -> dict:
    """대기 중인 /start·/stop 을 처리. 반환: {linked, stopped, seen}."""
    stats = {"linked": 0, "stopped": 0, "seen": 0}
    if not available():
        return stats
    offset = db.kv_get(OFFSET_KEY) or 0
    r = _call("getUpdates", {"offset": offset, "limit": limit, "timeout": 0})
    for up in (r or {}).get("result", []):
        db.kv_set(OFFSET_KEY, up.get("update_id", 0) + 1)
        msg = up.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            continue
        stats["seen"] += 1
        if text.startswith("/stop"):
            uid = _uid_by_chat(chat_id)
            if uid and unlink(uid):
                stats["stopped"] += 1
                send_message(chat_id, "브리핑을 껐습니다. 앱에서 다시 연결할 수 있어요.")
            continue
        if not text.startswith("/start"):
            continue
        code = text[len("/start"):].strip()
        uid = db.kv_get(f"tg_link:{code}", max_age=LINK_TTL) if code else None
        if not uid:
            send_message(chat_id, "연결 코드가 만료됐어요. 앱에서 '텔레그램 연결'을 다시 눌러 주세요.")
            continue
        _attach(int(uid), chat_id, msg)
        db.kv_set(f"tg_link:{code}", None)
        stats["linked"] += 1
        send_message(chat_id, "연결됐습니다. 이제 매일 아침 Nick이 후보 변화만 골라 보내드릴게요.\n"
                              "끄려면 /stop 을 보내세요.")
    return stats


def _uid_by_chat(chat_id: int) -> int | None:
    for u in db.users_with_telegram():
        if u["chat_id"] == chat_id:
            return u["id"]
    return None
