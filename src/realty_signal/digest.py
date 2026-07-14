"""관심지역 주간 이메일 다이제스트.

보유 데이터(시그널 스냅샷 diff · favorites)로 유저별 요약을 만들고,
SMTP env가 있으면 발송·없으면 dry-run 출력.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any

from realty_signal import db
from realty_signal.signals import history


def build_user_digest(
    email: str,
    regions: list[str],
    changes: list[dict],
    signal_map: dict[str, str],
    as_of: str,
) -> dict[str, Any]:
    """한 유저용 다이제스트 본문 데이터."""
    region_set = set(regions)
    my_changes = [c for c in changes if c.get("region") in region_set]
    lines = []
    for r in regions:
        sig = signal_map.get(r, "–")
        ch = next((c for c in my_changes if c["region"] == r), None)
        if ch:
            lines.append(f"· {r}: {ch['old']} → {ch['new']} ({ch.get('direction', '')})")
        else:
            lines.append(f"· {r}: {sig} (변화 없음)")
    subject = f"[Signal APT] 관심지역 주간 요약 ({as_of})"
    if my_changes:
        subject = f"[Signal APT] 관심지역 시그널 변동 {len(my_changes)}건 ({as_of})"
    body = (
        f"안녕하세요.\n\n"
        f"관심 지역 기준 주간 시그널 요약입니다. (데이터 기준일 {as_of})\n\n"
        + "\n".join(lines)
        + "\n\n"
        "상세·동네 리포트: https://signal-apt.up.railway.app/#dashboard\n"
        "※ 참고용이며 투자 권유가 아닙니다.\n"
    )
    return {
        "email": email,
        "subject": subject,
        "body": body,
        "regions": regions,
        "changes": my_changes,
        "as_of": as_of,
    }


def collect_digests(signal_df=None, changes: list[dict] | None = None, as_of: str | None = None) -> list[dict]:
    """관심지역 보유 유저 전원 다이제스트 생성."""
    import pandas as pd

    from realty_signal import store
    from realty_signal.signals.engine import SignalConfig, evaluate

    if signal_df is None:
        signal_df = evaluate(store.load(), SignalConfig(), store.load_supply())
    if as_of is None:
        as_of = str(store.load().last_date.date())
    if changes is None:
        changes = history.diff(history.load_snapshot(), signal_df)
    if isinstance(signal_df, pd.DataFrame):
        signal_map = dict(zip(signal_df["region"], signal_df["signal"]))
    else:
        signal_map = {}
    out = []
    for u in db.users_with_region_favs():
        out.append(build_user_digest(u["email"], u["regions"], changes, signal_map, as_of))
    return out


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_email(to: str, subject: str, body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    sender = os.environ["SMTP_FROM"]
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        if user:
            s.login(user, password)
        s.send_message(msg)


def run_digest(*, send: bool = False, quiet: bool = False) -> dict:
    """다이제스트 생성 + (옵션) 발송. 반환: sent/skipped/dry_run 카운트."""
    digests = collect_digests()
    stats = {"total": len(digests), "sent": 0, "skipped": 0, "dry_run": 0, "errors": 0}
    can_send = send and smtp_configured()
    for d in digests:
        if not can_send:
            stats["dry_run"] += 1
            if not quiet:
                print(f"--- {d['email']} ---\n{d['subject']}\n{d['body']}\n")
            continue
        try:
            send_email(d["email"], d["subject"], d["body"])
            stats["sent"] += 1
            db.kv_set(f"digest_sent:{d['email']}", {"as_of": d["as_of"]})
        except Exception:  # noqa: BLE001
            stats["errors"] += 1
    if send and not smtp_configured():
        stats["skipped"] = stats["total"]
    return stats
