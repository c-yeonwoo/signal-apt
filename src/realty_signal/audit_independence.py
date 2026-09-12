"""시그널 온셋 표본의 달력상 군집도를 재현 가능하게 계산한다.

지역 행 수는 같은 KB 주차에 함께 켜진 상속 신호를 여러 번 세므로, 독립 표본 수가 아니다.
여기서는 같은 주에 진입한 행을 하나의 보수적 '달력 군집'으로 묶는다. 이는 정식
통계적 독립성 검정이 아니라, 성적표의 행 수를 해석할 때 함께 봐야 할 최소 점검이다.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Iterable

import pandas as pd


def summarize_calendar_cohorts(onsets: Iterable[tuple[object, str]]) -> dict:
    """``(onset_date, region)`` 행을 같은 시작 주차의 군집으로 요약한다."""
    groups: dict[str, set[str]] = defaultdict(set)
    for at, region in onsets:
        if not region:
            continue
        day = pd.Timestamp(at).date().isoformat()
        groups[day].add(region)

    cohorts = [
        {"as_of": day, "regions": sorted(regions), "rows": len(regions)}
        for day, regions in sorted(groups.items())
    ]
    sizes = [item["rows"] for item in cohorts]
    by_year: dict[str, int] = defaultdict(int)
    for item in cohorts:
        by_year[item["as_of"][:4]] += 1

    return {
        "raw_onsets": sum(sizes),
        "calendar_cohorts": len(cohorts),
        "median_rows_per_cohort": median(sizes) if sizes else 0,
        "largest_cohort_rows": max(sizes, default=0),
        "cohorts_by_year": dict(sorted(by_year.items())),
        "cohorts": cohorts,
    }
