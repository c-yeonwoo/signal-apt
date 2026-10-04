"""Telegram notification readiness shown to the account owner."""

from __future__ import annotations

import time

from realty_signal import db, telegram


def _age_days(ts: float | None) -> float | None:
    return None if not ts else round((time.time() - ts) / 86400, 1)


def status(uid: int | None = None) -> dict:
    available = telegram.available()
    linked = bool(telegram.chat_id_of(db.profile_get(uid))) if uid else False
    last_ts = db.kv_ts(f"telegram_last_sent:{uid}") if uid else None
    channel = {
        "channel": "telegram",
        "label": "텔레그램 알림",
        "can_send": available and linked,
        "available": available,
        "linked": linked,
        "last_ts": last_ts,
        "last_days": _age_days(last_ts),
    }
    if not available:
        channel["reason"] = "서버에 텔레그램 봇 토큰이 없어 알림을 보낼 수 없습니다."
        channel["how"] = "운영자가 TELEGRAM_BOT_TOKEN 을 설정해야 합니다."
    elif not linked:
        channel["reason"] = "아직 텔레그램을 연결하지 않았습니다."
        channel["how"] = "텔레그램 연결을 눌러 일회용 코드로 연결하세요."
    blocked = [] if channel["can_send"] else [channel]
    return {
        "channels": [channel], "blocked": blocked, "all_ready": not blocked,
        "note": ("알림이 안 오는 이유는 시장이 조용한 것과 다릅니다. 서버 설정을 확인하세요."
                 if not available else
                 "텔레그램 알림은 선택 기능입니다. 연결하면 확인된 변화만 받습니다."
                 if not linked else "텔레그램이 연결돼 있습니다. 변화가 확인되면 알립니다."),
    }
