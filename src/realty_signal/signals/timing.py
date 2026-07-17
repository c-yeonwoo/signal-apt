"""타이밍 점수 — 지역·매물 레이어 공통 Decision Plane (v1).

기회도(v2) 휴리스틱을 흡수하되, confidence·source·asof·version 메타를 붙인다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERSION = "v1"

_SIG_BONUS = {"STRONG_BUY": 25, "BUY": 15, "WATCH": 5, "NEUTRAL": 0, "SELL_RISK": -20}
_GRADE_BONUS = {"A": 8, "B": 4, "C": 0, "D": -4}
_SIG_SCORE = {"STRONG_BUY": 88, "BUY": 72, "WATCH": 52, "NEUTRAL": 42, "SELL_RISK": 18}


@dataclass
class TimingResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.7
    source: str = "kb_weekly"
    asof: str | None = None
    layer: str = "listing"
    version: str = VERSION

    @property
    def reasons_text(self) -> str:
        return " · ".join(self.reasons)

    def to_dict(self) -> dict:
        """API·UI 공통 필드. 기회도 키는 하위 호환."""
        return {
            "타이밍점수": self.score,
            "타이밍근거": self.reasons_text,
            "confidence": round(self.confidence, 2),
            "source": self.source,
            "asof": self.asof,
            "layer": self.layer,
            "timing_version": self.version,
            "기회도": self.score,
            "기회도근거": self.reasons_text,
        }


def _clamp(n: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, n))


def _listing_base(kind: str, raw: dict) -> tuple[int, list[str], float]:
    """유형별 고유 할인/기대(0~60) + 신뢰도 보정."""
    why: list[str] = []
    conf = 0.72
    if kind == "급매":
        g = raw.get("급매갭")
        if g is None:
            base, why = 0, ["급매갭 –"]
            conf = 0.45
        elif g <= -35:
            base, why = 15, [f"급매갭 {g}%(⚠️비현실적·확인필요)"]
            conf = 0.35
        elif g < 0:
            base, why = min(60, round(-g * 2)), [f"급매갭 {g}%"]
            conf = 0.78 if g >= -30 else 0.55
        else:
            base, why = 0, [f"급매갭 {g}%(시세 이상)"]
            conf = 0.5
    elif kind == "경매":
        r = raw.get("시세차익률")
        base = 0 if r is None else max(0, min(60, round(r * 1.8)))
        why.append(f"시세차익 {r}%" if r is not None else "시세차익 –")
        conf = 0.68 if r is not None else 0.4
    elif kind == "청약":
        st, dd = raw.get("상태"), raw.get("Dday")
        base = 45 if st == "접수중" else 35 if st == "접수예정" else 20
        if dd is not None and 0 <= dd <= 14:
            base += 15 - dd
        why.append(f"청약 {st}" + (f" D-{dd}" if dd is not None and dd >= 0 else ""))
        conf = 0.55
    elif kind == "재건축":
        p = raw.get("잠재력")
        base = 0 if p is None else round(p * 0.55)
        why.append(f"재건축 잠재력 {p}" if p is not None else "재건축 잠재력 –")
        conf = 0.5 if p is None else 0.62
    else:
        base, why, conf = 0, [], 0.5
    return base, why, conf


def listing_timing(
    kind: str,
    raw: dict,
    signal: str | None,
    grade: str | None,
    *,
    asof: str | None = None,
    source: str = "listings_merged",
) -> TimingResult:
    """매물(경매·급매·청약·재건축) 타이밍 점수."""
    base, why, conf = _listing_base(kind, raw)
    sb = _SIG_BONUS.get(signal or "", 0)
    gb = _GRADE_BONUS.get(grade or "", 0)
    if signal:
        why.append(f"{signal}({sb:+d})")
        if signal in ("STRONG_BUY", "BUY"):
            conf = min(0.95, conf + 0.08)
        elif signal == "SELL_RISK":
            conf = min(0.95, conf + 0.05)
    else:
        conf = max(0.3, conf - 0.12)
    if grade:
        why.append(f"{grade}급지({gb:+d})")
    score = _clamp(base + sb + gb)
    return TimingResult(
        score=score, reasons=why, confidence=conf,
        source=source, asof=asof, layer="listing",
    )


def region_timing(
    signal: str | None,
    *,
    asof: str | None = None,
    jeonse_supply: float | None = None,
    sale_momentum: str | None = None,
    backtest_up_pct: float | None = None,
    market_strength: int | None = None,
    source: str = "kb_weekly",
) -> TimingResult:
    """지역(KB 주간) 타이밍 — 시그널 강도 + (선택) 백테스트·시장강도."""
    why: list[str] = []
    base = _SIG_SCORE.get(signal or "", 45)
    why.append(f"지역시그널 {signal or '–'}")
    conf = 0.82 if signal else 0.5
    if jeonse_supply is not None:
        why.append(f"전세수급 {jeonse_supply:.0f}")
    if sale_momentum:
        why.append(f"매매모멘텀 {sale_momentum}")
        if sale_momentum == "상승":
            conf = min(0.92, conf + 0.05)
    if backtest_up_pct is not None:
        why.append(f"12주 적중률 {backtest_up_pct:.0f}%")
        base = _clamp(round(base * 0.7 + backtest_up_pct * 0.3))
        conf = min(0.9, conf + 0.04)
    if market_strength is not None:
        why.append(f"시장강도 {market_strength}")
        # 시장강도는 ±8점 보정 (과적합 방지)
        base = _clamp(round(base + (market_strength - 50) * 0.16))
        conf = min(0.92, conf + 0.03)
    return TimingResult(
        score=_clamp(base), reasons=why, confidence=conf,
        source=source, asof=asof, layer="region",
    )
