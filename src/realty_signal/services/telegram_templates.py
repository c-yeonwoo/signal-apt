"""Short plain-text Telegram templates for verified, user-specific updates."""

from __future__ import annotations

from realty_signal import config
from realty_signal.services.complex_watch import _LAG_NOTE
from realty_signal.time_kst import today_kst


def _short(value, limit: int = 54) -> str:
    return str(value or "확인 필요").replace("\n", " ").strip()[:limit]


def _eok(value) -> str:
    try:
        return f"{float(value) / 10000:,.2f}억"
    except (TypeError, ValueError):
        return "가격 확인 필요"


def render_updates(complexes: list[dict], events: list[dict]) -> str:
    """One message per account/run avoids a burst of adjacent chat messages."""
    parts = [f"📬 Signal APT 관심 소식 · {today_kst().isoformat()}"]
    if complexes:
        parts.extend(["", "🏘 관심단지 변화"])
        for item in complexes[:3]:
            parts.append(f"• {_short(item.get('단지명'))} ({_short(item.get('지역'), 24)})")
            parts.extend(f"  - {_short(change.get('말'), 90)}" for change in (item.get("changes") or [])[:2])
        parts.append(f"※ {_LAG_NOTE}")
    if events:
        parts.extend(["", "🔔 찜한 항목 알림"])
        for item in events[:3]:
            payload = item.get("payload") or {}
            name = _short(payload.get("name"))
            region = _short(payload.get("region"), 24)
            kind = item.get("kind")
            if kind == "presale_deadline":
                parts.append(f"• [청약 일정] {name} ({region}) · 다음 일정 오늘({payload.get('date')})")
                if payload.get("status"):
                    parts.append(f"  - 공고 표시 상태: {_short(payload['status'], 45)}")
                parts.append("  - 자격·접수 가능 여부는 모집공고에서 직접 확인하세요.")
            elif kind == "listing_price":
                parts.append(f"• [호가 변화] {name} ({region}) · "
                             f"{_eok(payload.get('old_price'))} → {_eok(payload.get('new_price'))}")
                parts.append("  - 수집 호가 변화이며 현재 판매 여부는 확인되지 않았습니다.")
            elif kind == "listing_target":
                parts.append(f"• [목표 호가] {name} ({region}) · 현재 {_eok(payload.get('new_price'))} "
                             f"(설정 {_eok(payload.get('target_price'))})")
                parts.append("  - 수집 호가 기준이며 거래 가능 여부는 별도 확인하세요.")
            elif kind == "new_alternative":
                names = ", ".join(_short(alt.get("name"), 30)
                                  for alt in (payload.get("alternatives") or [])[:2])
                parts.append(f"• [새 대안] {name} ({region}) · {names or '앱에서 확인'}")
                parts.append("  - 현재 수집 범위의 후보이며 추천·판매 확정이 아닙니다.")
    parts.extend(["", f"앱에서 확인: {config.app_base_url()}/#watch", "알림 끄기: /stop"])
    return "\n".join(parts)
