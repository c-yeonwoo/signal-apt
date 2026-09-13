"""V-2 — 국토부 실거래가로 KB 시그널 성적표를 교차 검증한다.

실행: PYTHONPATH=src .venv/bin/python scripts/audit/v2_real_trade.py
수집: signal v2-collect (일일 호출 한도에 걸리면 다음 날 이어서 실행)
"""

from realty_signal import store
from realty_signal.real_trade_validation import summary
from realty_signal.services import market_data as md


result = summary(md.kb(), store.V2_REAL_TRADE_DIR, md.signal_config())
coverage = result["coverage"]
print("===== V-2 국토부 실거래가 교차 검증 =====")
print(f"가격 시계열 지역 {coverage['가격시계열지역수']}/{coverage['KB지역수']} · "
      f"{coverage['horizon_months']}개월 후 · 월 거래 {coverage['min_transactions']}건 이상")
if not result["ready"]:
    print("평가 가능한 신호 구간이 없습니다. 수집 범위와 월별 거래 수를 확인하세요.")
else:
    print(f"{'신호':12s} {'평가수':>6s} {'적중률':>8s} {'시장':>8s} {'리프트':>8s} "
          f"{'수익':>9s} {'시장수익':>9s} {'초과':>9s}")
    for row in result["by_signal"]:
        rate = row.get("상승률", row.get("하락률"))
        print(f"{row['signal']:12s} {row['평가수']:6,} {rate:7.1f}% {row['시장평균']:7.1f}% "
              f"{row['리프트']:+7.1f}%p {row['평균수익']:+8.2f}% "
              f"{row['시장평균수익']:+8.2f}% {row['초과수익']:+8.2f}%p")
print("주의:")
for note in result["notes"]:
    print("-", note)
