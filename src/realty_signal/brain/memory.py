"""Nick episodic memory — 대화에서 추출한 관심 맥락 (LLM 호출 없이).

저장: db.kv nick_memory:{uid}
개인정보 최소화 — 지역·단지·질문 요약 토큰만, 원문 대화 전체 미저장.
"""

from __future__ import annotations

import re
import time
from typing import Any

from realty_signal import db

KV_PREFIX = "nick_memory:"
MAX_REGIONS = 8
MAX_COMPLEXES = 6
MAX_NOTES = 5
MAX_TURNS_DIGEST = 6


def _key(uid: int) -> str:
    return f"{KV_PREFIX}{uid}"


def load(uid: int) -> dict:
    v = db.kv_get(_key(uid))
    return v if isinstance(v, dict) else {
        "regions": [], "complexes": [], "notes": [], "updated_ts": None, "turns": 0,
    }


def save(uid: int, mem: dict) -> dict:
    mem = dict(mem)
    mem["updated_ts"] = int(time.time())
    db.kv_set(_key(uid), mem)
    return mem


def clear(uid: int) -> None:
    db.kv_set(_key(uid), {
        "regions": [], "complexes": [], "notes": [], "updated_ts": int(time.time()), "turns": 0,
    })


def _uniq_keep(seq: list[str], limit: int) -> list[str]:
    seen, out = set(), []
    for x in seq:
        x = (x or "").strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out[:limit]


def extract_from_text(text: str, known_regions: list[str] | None = None) -> dict[str, list[str]]:
    """메시지에서 지역·단지 후보 추출 (규칙 기반)."""
    t = text or ""
    regions: list[str] = []
    known = sorted(known_regions or [], key=len, reverse=True)
    for r in known:
        if r and r in t:
            regions.append(r)
    # 흔한 구/시 패턴 폴백
    for m in re.finditer(r"([가-힣]{1,10}(?:구|시|군))", t):
        regions.append(m.group(1))
    complexes: list[str] = []
    for m in re.finditer(r"([가-힣A-Za-z0-9]{2,20}(?:아파트|자이|힐스테이트|푸르지오|래미안|아이파크|더샵|센트럴))", t):
        complexes.append(m.group(1))
    # "○○ 단지" 단순 패턴
    for m in re.finditer(r"([가-힣]{2,12})\s*(?:단지|아파트)", t):
        complexes.append(m.group(1))
    return {
        "regions": _uniq_keep(regions, MAX_REGIONS),
        "complexes": _uniq_keep(complexes, MAX_COMPLEXES),
    }


def _note_from_user(text: str) -> str | None:
    t = (text or "").strip().replace("\n", " ")
    if len(t) < 8:
        return None
    if len(t) > 80:
        t = t[:77] + "…"
    return t


def update_from_messages(
    uid: int,
    messages: list[dict],
    *,
    known_regions: list[str] | None = None,
    answer: str | None = None,
) -> dict:
    """대화 턴으로 메모리 갱신. 사용자 발화 위주 + 답변에서 지역 보강."""
    mem = load(uid)
    regions = list(mem.get("regions") or [])
    complexes = list(mem.get("complexes") or [])
    notes = list(mem.get("notes") or [])
    turns = int(mem.get("turns") or 0) + 1

    texts = []
    for m in (messages or [])[-MAX_TURNS_DIGEST:]:
        role = m.get("role") or ""
        text = m.get("text") or m.get("content") or ""
        if text:
            texts.append((role, text))
    blob = " ".join(t for _, t in texts)
    if answer:
        blob += " " + answer

    ex = extract_from_text(blob, known_regions)
    regions = _uniq_keep(ex["regions"] + regions, MAX_REGIONS)
    complexes = _uniq_keep(ex["complexes"] + complexes, MAX_COMPLEXES)

    # 최근 사용자 질문 한 줄 노트
    for role, text in reversed(texts):
        if role == "user":
            n = _note_from_user(text)
            if n and n not in notes:
                notes.insert(0, n)
            break
    notes = notes[:MAX_NOTES]

    return save(uid, {
        "regions": regions, "complexes": complexes, "notes": notes, "turns": turns,
    })


def format_for_system(mem: dict | None) -> str:
    """system prompt 에 붙일 짧은 블록."""
    m = mem or {}
    bits = []
    if m.get("regions"):
        bits.append("최근 대화 관심지역 " + ", ".join(m["regions"][:MAX_REGIONS]))
    if m.get("complexes"):
        bits.append("최근 대화 관심단지 " + ", ".join(m["complexes"][:MAX_COMPLEXES]))
    if m.get("notes"):
        bits.append("최근 질문: " + " / ".join(m["notes"][:3]))
    if not bits:
        return ""
    return (
        "\n최근 대화 기억(참고용·틀리면 사용자 말을 우선):\n- "
        + "\n- ".join(bits) + "\n"
    )


def to_public(mem: dict) -> dict[str, Any]:
    return {
        "regions": mem.get("regions") or [],
        "complexes": mem.get("complexes") or [],
        "notes": mem.get("notes") or [],
        "turns": mem.get("turns") or 0,
        "updated_ts": mem.get("updated_ts"),
    }
