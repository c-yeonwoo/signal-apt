"""알림 채널 상태 — 왜 안 나가는지 화면이 직접 말한다.

2026-09-06 조사: 이메일 다이제스트와 텔레그램 브리핑이 7/26 이후 멈춰 있었다.
코드는 멀쩡했다 — 설정(SMTP·봇 토큰)이 없어서 나가지 않았고, **그 사실이 어디에도
표시되지 않았다.** 사용자는 "알림을 켰는데 왜 안 오지" 하게 된다.

이 모듈은 채널마다 세 가지를 답한다:
    · 지금 나갈 수 있나 (`can_send`)
    · 못 나가면 **왜** (`reason` + `how`)
    · 마지막으로 언제 나갔나 (`last_ts`, dry-run 이었는지까지)

`텔레그램 브리핑은 준비 중입니다` 같은 문구를 쓰지 않는다 — 준비 중이 아니라
서버에 토큰이 없는 것이고, 둘은 사용자가 취할 행동이 다르다.
"""

from __future__ import annotations

import os
import time

from realty_signal import db

DIGEST_STATS_KEY = "last_digest_stats"


def _age_days(ts: float | None) -> float | None:
    return None if not ts else round((time.time() - ts) / 86400, 1)


def _email(uid: int | None) -> dict:
    from realty_signal import digest as dig

    configured = dig.smtp_configured()
    stats = db.kv_get(DIGEST_STATS_KEY) or {}
    last_run = db.kv_get("last_digest_run")
    me_ready = False
    if uid:
        me_ready = bool([f for f in db.fav_list(uid) if f["kind"] == "region"])
    out = {
        "channel": "email",
        "label": "이메일 주간 다이제스트",
        "can_send": configured,
        "last_ts": last_run,
        "last_days": _age_days(last_run),
        "last_stats": {k: stats.get(k) for k in ("total", "sent", "dry_run", "errors")}
        if stats else None,
        "me_ready": me_ready,
    }
    if not configured:
        out["reason"] = "서버에 메일 발송 설정(SMTP)이 없어 실제로 발송되지 않습니다."
        out["how"] = "환경변수 SMTP_HOST · SMTP_FROM 을 설정하면 발송이 켜집니다."
    elif not me_ready:
        out["reason"] = "관심지역이 없어 보낼 내용이 없습니다."
        out["how"] = "관심지역을 ★ 로 등록하면 주간 다이제스트가 갑니다."
    return out


def _telegram(uid: int | None) -> dict:
    available = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    linked = False
    if uid:
        prof = db.profile_get(uid) or {}
        linked = bool(prof.get("telegram_chat_id"))
    # briefing_run:{날짜} 중 가장 최근 것 — 기존 kv 헬퍼를 쓴다
    keys = sorted(db.kv_keys("briefing_run:"))
    last_run_key = keys[-1] if keys else None
    last_ts = db.kv_max_ts("briefing_run:")
    out = {
        "channel": "telegram",
        "label": "텔레그램 데일리 브리핑",
        "can_send": available and linked,
        "available": available,
        "linked": linked,
        "last_ts": last_ts,
        "last_days": _age_days(last_ts),
        "last_run_key": last_run_key,
    }
    if not available:
        out["reason"] = "서버에 텔레그램 봇 토큰이 없어 브리핑을 보낼 수 없습니다."
        out["how"] = "@BotFather 로 봇을 만들고 환경변수 TELEGRAM_BOT_TOKEN 을 설정하세요."
    elif not linked:
        out["reason"] = "아직 텔레그램을 연결하지 않았습니다."
        out["how"] = "[텔레그램 연결] 을 눌러 일회용 코드로 연결하세요."
    return out


def status(uid: int | None = None) -> dict:
    """채널별 상태. 하나라도 못 나가면 `blocked` 에 담아 화면이 먼저 보여준다."""
    chans = [_email(uid), _telegram(uid)]
    blocked = [c for c in chans if not c["can_send"]]
    return {
        "channels": chans,
        "blocked": blocked,
        "all_ready": not blocked,
        # 알림이 안 오는 게 '시장이 조용해서' 가 아니라는 걸 구분해 준다
        "note": ("알림이 안 오는 이유는 시장이 조용한 것과 다릅니다. "
                 "아래 사유를 확인하세요." if blocked else
                 "알림 채널이 모두 정상입니다."),
    }


def record_digest_run(stats: dict, *, sent: bool) -> None:
    """다이제스트 실행 결과를 남긴다 — 예전엔 로그로만 흘려 dry-run 5주를 아무도 몰랐다."""
    db.kv_set(DIGEST_STATS_KEY, {**(stats or {}), "sent": stats.get("sent", 0),
                                 "was_dry_run": not sent, "ts": time.time()})
