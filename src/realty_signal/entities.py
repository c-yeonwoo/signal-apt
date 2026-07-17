"""Canonical entity / provenance 스키마 (Phase 3).

DB 테이블을 새로 두지 않고, API·파이프라인·캐시 응답에 붙이는
공통 dict/dataclass 계약. 필드 누락은 None — 추정 금지.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SourceStatus = Literal["ok", "partial", "stale", "missing"]


@dataclass
class Provenance:
    """데이터 출처·신선도."""
    source: str
    asof: str | None = None
    fetched_ts: int | None = None
    status: SourceStatus = "ok"
    confidence: float = 0.7
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class RegionEntity:
    """지역(시군구/광역) 의사결정용 정규 뷰."""
    region: str
    signal: str | None = None
    timing_score: int | None = None
    market_strength: int | None = None
    market_strength_label: str | None = None
    volume_ratio: float | None = None
    quicksale_count: int | None = None
    supply_pressure: float | None = None
    grade: str | None = None
    provenance: Provenance | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"region": self.region}
        for k in ("signal", "timing_score", "market_strength", "market_strength_label",
                  "volume_ratio", "quicksale_count", "supply_pressure", "grade"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.provenance:
            d["provenance"] = self.provenance.to_dict()
        if self.extras:
            d.update(self.extras)
        return d


@dataclass
class ListingEntity:
    """통합 매물 정규 뷰 (기회도/타이밍 하위 호환 필드 유지)."""
    kind: str
    name: str
    region: str
    timing_score: int | None = None
    confidence: float | None = None
    asof: str | None = None
    source: str = "listings_merged"
    total: float | None = None
    ref: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "유형": self.kind, "단지명": self.name, "지역": self.region,
            "source": self.source,
        }
        if self.timing_score is not None:
            d["타이밍점수"] = self.timing_score
            d["기회도"] = self.timing_score
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.asof is not None:
            d["asof"] = self.asof
        if self.total is not None:
            d["총액"] = self.total
        if self.ref:
            d["ref"] = self.ref
        return d
