"""V-3: STRONG_BUY 52주 온셋 행이 실제로 몇 개의 달력 군집인지 측정한다.

실행: PYTHONPATH=src .venv/bin/python scripts/audit/verify_independence.py

같은 주에 여러 지역이 진입하면 KB 수급 지표 상속 등 공통 요인이 있을 수 있다. 따라서
393 같은 지역-온셋 행 수를 독립 관측 수처럼 읽지 않고, 같은 시작 주차를 한 군집으로
함께 보고한다. 이는 정식 독립성 검정이 아닌 보수적 진단용 대리척도다.
"""

from realty_signal.audit_independence import summarize_calendar_cohorts
from realty_signal.services import market_data as md
from realty_signal.signals.engine import price_index_from, signal_history

SIGNAL = "STRONG_BUY"
HORIZON_WEEKS = 52


def has_forward_window(index, at) -> bool:
    return len(index[index.index > at]) >= HORIZON_WEEKS


kb = md.kb()
cfg = md.signal_config()
onsets: list[tuple[object, str]] = []
for region in kb.latest().index:
    sale = kb.series(region, "sale_change")
    if sale.empty:
        continue
    index = price_index_from(sale)
    try:
        intervals = signal_history(kb, region, cfg)
    except Exception:  # noqa: BLE001 - one malformed region must not hide the cohort count
        continue
    for interval in intervals:
        if interval.get("signal") != SIGNAL:
            continue
        at = interval.get("start")
        if at and has_forward_window(index, at):
            onsets.append((at, region))

summary = summarize_calendar_cohorts(onsets)
print(f"===== V-3 {SIGNAL} 온셋 · {HORIZON_WEEKS}주 후 관측 가능 표본 =====")
print(f"지역-온셋 행: {summary['raw_onsets']:,}")
print(f"같은 주차 진입 군집: {summary['calendar_cohorts']:,}")
print(f"군집당 지역 수: 중앙값 {summary['median_rows_per_cohort']:.1f} · 최대 {summary['largest_cohort_rows']}")
print("연도별 군집:", ", ".join(f"{year} {count}" for year, count in summary["cohorts_by_year"].items()))
print("주의: 같은 주차 군집은 상속·공통 시장 충격을 보수적으로 묶는 대리척도다.\n"
      "이 숫자 역시 정식 독립 표본 수나 신뢰구간으로 해석하지 않는다.")
