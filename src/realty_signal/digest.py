"""관심지역 주간 이메일 다이제스트.

보유 데이터(시그널 스냅샷 diff · favorites)로 유저별 요약을 만들고,
SMTP env가 있으면 발송·없으면 dry-run 출력.
"""

from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from realty_signal import db
from realty_signal.brain import snapshots


def build_user_digest(
    email: str,
    regions: list[str],
    changes: list[dict],
    signal_map: dict[str, str],
    as_of: str,
    extras: dict | None = None,
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
    extra_lines = []
    ex = extras or {}
    # 거시
    macro = ex.get("macro") or {}
    if macro.get("대출금리") is not None or macro.get("구매력") is not None:
        bits = []
        if macro.get("대출금리") is not None:
            bits.append(f"주담대 {macro['대출금리']}%")
        if macro.get("구매력") is not None:
            bits.append(f"구매력 {macro['구매력']}")
        if macro.get("기준"):
            bits.append(f"기준 {macro['기준']}")
        extra_lines.append("거시: " + " · ".join(bits))
    # 관심지역 거래량비
    vols = ex.get("volumes") or {}
    vol_bits = [f"{r} {vols[r]}" for r in regions if vols.get(r) is not None]
    if vol_bits:
        extra_lines.append("거래량비: " + ", ".join(vol_bits[:6]))
    # 관심단지 전세가율·갭
    cxs = ex.get("complexes") or []
    for c in cxs[:5]:
        bits = [c.get("name") or ""]
        if c.get("전세가율") is not None:
            bits.append(f"전세가율 {c['전세가율']}%")
        if c.get("갭") is not None:
            g = c["갭"]
            bits.append(f"갭 {g/10000:.1f}억" if g >= 10000 else f"갭 {g}만")
        if c.get("급매"):
            bits.append(f"급매 {c['급매']}건")
        extra_lines.append("· " + " · ".join(bits))
    subject = f"[Signal APT] 관심지역 주간 요약 ({as_of})"
    if my_changes:
        subject = f"[Signal APT] 관심지역 시그널 변동 {len(my_changes)}건 ({as_of})"
    body = (
        f"안녕하세요.\n\n"
        f"관심 지역 기준 주간 시그널 요약입니다. (데이터 기준일 {as_of})\n\n"
        + "\n".join(lines)
        + ("\n\n— 보조 —\n" + "\n".join(extra_lines) if extra_lines else "")
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

    from realty_signal import personal_layer as pl
    from realty_signal import store
    from realty_signal.signals.engine import SignalConfig, evaluate

    if signal_df is None:
        signal_df = evaluate(store.load(), SignalConfig(), store.load_supply())
    if as_of is None:
        as_of = str(store.load().last_date.date())
    if changes is None:
        changes = snapshots.diff(snapshots.load(), signal_df)
    if isinstance(signal_df, pd.DataFrame):
        signal_map = dict(zip(signal_df["region"], signal_df["signal"]))
    else:
        signal_map = {}
    macro = pl.macro_latest()
    # 급매 지역별 건수
    qs_by: dict[str, int] = {}
    try:
        qs_path = Path("data/cache/quicksale.json")
        if qs_path.exists():
            qs = json.loads(qs_path.read_text(encoding="utf-8")).get("listings", [])
            for m in qs:
                r = m.get("지역") or ""
                if r:
                    qs_by[r] = qs_by.get(r, 0) + 1
    except Exception:  # noqa: BLE001
        qs_by = {}
    out = []
    for u in db.users_with_region_favs():
        vols = {r: (pl.volume_summary(r) or {}).get("거래량비") for r in u["regions"]}
        complexes = []
        codes = {}
        try:
            codes = json.loads(store.CODES_FILE.read_text(encoding="utf-8")) if store.CODES_FILE.exists() else {}
        except Exception:  # noqa: BLE001
            codes = {}
        for f in db.fav_list(u["id"]):
            if f.get("kind") != "complex":
                continue
            key = f.get("key") or ""
            reg, _, nm = key.partition("|")
            if not nm:
                continue
            code = codes.get(reg) or ""
            d = db.kv_get(f"complex:{code[:5]}:{nm}", max_age=30 * 86400) if code[:5].isdigit() else None
            plist = (d or {}).get("평형별") or []
            main = max(plist, key=lambda p: p.get("매매건수", 0) or 0) if plist else {}
            complexes.append({
                "name": nm, "region": reg,
                "전세가율": main.get("전세가율"), "갭": main.get("갭"),
                "급매": qs_by.get(reg),
            })
        extras = {"macro": macro, "volumes": vols, "complexes": complexes}
        out.append(build_user_digest(u["email"], u["regions"], changes, signal_map, as_of, extras))
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
