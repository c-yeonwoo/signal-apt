"""시장강도 프록시 — 거래량비 + 급매 밀도 (부동산지인/아실 대체).

공식 크롤 없이 보유 데이터만으로 0~100 점수·라벨 산출.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrengthResult:
    score: int
    label: str
    reasons: list[str]
    volume_ratio: float | None = None
    quicksale_count: int | None = None
    confidence: float = 0.6
    source: str = "volume_quicksale_proxy"

    def to_dict(self) -> dict:
        return {
            "시장강도": self.score,
            "시장강도라벨": self.label,
            "시장강도근거": " · ".join(self.reasons),
            "거래량비": self.volume_ratio,
            "급매건수": self.quicksale_count,
            "confidence": round(self.confidence, 2),
            "source": self.source,
        }


def _label(score: int) -> str:
    if score >= 75:
        return "강세"
    if score >= 55:
        return "활발"
    if score >= 40:
        return "보통"
    if score >= 25:
        return "위축"
    return "침체"


def market_strength(
    *,
    volume_ratio: float | None,
    quicksale_count: int | None = None,
    signal: str | None = None,
) -> StrengthResult:
    """거래량비(주) + 급매 건수(보조) + 시그널(소폭)."""
    why: list[str] = []
    score = 45.0
    conf = 0.45

    if volume_ratio is not None:
        conf = 0.72
        # 1.0 = 평년, 1.2↑ 급증, 0.8↓ 위축
        if volume_ratio >= 1.5:
            score = 82.0
            why.append(f"거래량비 {volume_ratio}배(급증)")
        elif volume_ratio >= 1.2:
            score = 70.0
            why.append(f"거래량비 {volume_ratio}배(확대)")
        elif volume_ratio >= 1.0:
            score = 55.0
            why.append(f"거래량비 {volume_ratio}배")
        elif volume_ratio >= 0.8:
            score = 40.0
            why.append(f"거래량비 {volume_ratio}배(소폭↓)")
        else:
            score = 22.0
            why.append(f"거래량비 {volume_ratio}배(위축)")
            conf = min(0.8, conf + 0.05)
    else:
        why.append("거래량비 –")

    qc = quicksale_count if quicksale_count is not None else 0
    if quicksale_count is not None:
        # 급매 많음 = 매도압력·유동성 기회 공존 → 중간 가점(과열 아님)
        if qc >= 8:
            score = min(100, score + 8)
            why.append(f"급매 {qc}건(다수)")
            conf = min(0.85, conf + 0.06)
        elif qc >= 3:
            score = min(100, score + 4)
            why.append(f"급매 {qc}건")
            conf = min(0.8, conf + 0.04)
        elif qc == 0 and volume_ratio is not None:
            why.append("급매 0건")
        else:
            why.append(f"급매 {qc}건")

    if signal in ("STRONG_BUY", "BUY"):
        score = min(100, score + 5)
        why.append(f"시그널 {signal}")
    elif signal == "SELL_RISK":
        score = max(0, score - 10)
        why.append(f"시그널 {signal}")

    s = max(0, min(100, round(score)))
    return StrengthResult(
        score=s, label=_label(s), reasons=why,
        volume_ratio=volume_ratio, quicksale_count=quicksale_count,
        confidence=conf,
    )
