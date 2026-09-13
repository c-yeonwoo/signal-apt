"""V-2 — KB 시그널을 국토부 월별 실거래 평단가로 교차 검증한다.

이 모듈은 시그널·추천을 바꾸지 않는다. KB 주간 지수 성적표가 실제 거래 가격에서도
재현되는지를 감사하는 전용 도구다. 시그널 종료 월의 거래를 쓰면 그 달 후반 거래를
미리 본 셈이 될 수 있어, 항상 **다음 달**을 시작점으로 삼는다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

import pandas as pd

from realty_signal.signals.engine import SignalConfig, signal_history


def shift_month(ym: str, months: int) -> str:
    """YYYYMM에 달 수를 더한다."""
    year, month = int(ym[:4]), int(ym[4:])
    offset = year * 12 + month - 1 + months
    return f"{offset // 12:04d}{offset % 12 + 1:02d}"


def next_month_of(date: str) -> str:
    """주간 시그널 종료 뒤 첫 완결 월. 같은 달 거래로 인한 룩어헤드를 피한다."""
    return shift_month(pd.Timestamp(date).strftime("%Y%m"), 1)


def _prices(cache_dir: Path, lawd: str, min_transactions: int) -> dict[str, float]:
    """신뢰 가능한 월별 중위 평단가만 읽는다. 원자료 거래 목록은 쓰지 않는다."""
    out = {}
    for path in sorted((cache_dir / lawd).glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            price, n = row.get("median_ppy"), row.get("transactions", 0)
            if price is not None and int(n or 0) >= min_transactions:
                out[str(row.get("ym") or path.stem)] = float(price)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return out


def _return_at(prices: dict[str, float], start_ym: str, horizon_months: int) -> float | None:
    """완결 월 시작점에서 horizon 뒤 완결 월까지의 평단가 변화율(%)."""
    end_ym = shift_month(start_ym, horizon_months)
    start, end = prices.get(start_ym), prices.get(end_ym)
    if start is None or end is None or start <= 0:
        return None
    return (end / start - 1) * 100


def summary(
    kb,
    cache_dir: Path,
    config: SignalConfig | None = None,
    *,
    horizon_months: int = 3,
    min_transactions: int = 5,
) -> dict:
    """실거래 V-2 성적표.

    매 신호 구간의 종료 다음 달부터 3개월 수익을 구하고, 같은 시작월에 관측 가능한
    모든 시군구의 평균과 비교한다. `평가수`는 지역×시그널 구간 행 수이지 독립 표본
    수가 아니므로, 해석용 참고치로만 반환한다.
    """
    if horizon_months < 1:
        raise ValueError("horizon_months는 1 이상이어야 합니다")
    if min_transactions < 1:
        raise ValueError("min_transactions는 1 이상이어야 합니다")

    codes = getattr(kb, "codes", {}) or {}
    price_by_region: dict[str, dict[str, float]] = {}
    for region in kb.latest().index:
        code = str(codes.get(region) or "")
        if len(code) < 5 or not code[:5].isdigit() or code[2:5] == "000":
            continue
        prices = _prices(cache_dir, code[:5], min_transactions)
        if prices:
            price_by_region[region] = prices

    # 같은 시작월의 시장 기준선. 신호가 없는 지역도 포함해야 base rate가 된다.
    market: dict[str, list[float]] = defaultdict(list)
    for prices in price_by_region.values():
        for start_ym in prices:
            ret = _return_at(prices, start_ym, horizon_months)
            if ret is not None:
                market[start_ym].append(ret)

    samples: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for region, prices in price_by_region.items():
        try:
            intervals = signal_history(kb, region, config)
        except Exception:  # noqa: BLE001 - 한 지역 이력 오류가 전체 감사 결과를 숨기면 안 된다
            continue
        for interval in intervals:
            signal, end = interval.get("signal"), interval.get("end")
            if signal not in {"STRONG_BUY", "BUY", "SELL"} or not end:
                continue
            start_ym = next_month_of(str(end))
            ret = _return_at(prices, start_ym, horizon_months)
            if ret is not None and market.get(start_ym):
                samples[signal].append((start_ym, ret))

    by_signal = []
    for signal in ("STRONG_BUY", "BUY", "SELL"):
        rows = samples.get(signal, [])
        if not rows:
            continue
        returns = [ret for _, ret in rows]
        bases = [market[start] for start, _ in rows]
        buying = signal != "SELL"
        hit = mean((ret > 0) if buying else (ret < 0) for ret in returns) * 100
        market_hit = mean(
            mean((ret > 0) if buying else (ret < 0) for ret in base)
            for base in bases
        ) * 100
        market_return = mean(mean(base) for base in bases)
        by_signal.append({
            "signal": signal,
            "평가수": len(rows),
            "상승률" if buying else "하락률": round(hit, 1),
            "시장평균": round(market_hit, 1),
            "리프트": round(hit - market_hit, 1),
            "평균수익": round(mean(returns), 2),
            "시장평균수익": round(market_return, 2),
            "초과수익": round(mean(returns) - market_return, 2),
        })

    return {
        "ready": bool(by_signal),
        "by_signal": by_signal,
        "coverage": {
            "가격시계열지역수": len(price_by_region),
            "KB지역수": len(kb.latest().index),
            "horizon_months": horizon_months,
            "min_transactions": min_transactions,
        },
        "notes": [
            "시그널 종료 다음 달부터의 월별 실거래 중위 평단가만 사용해 룩어헤드를 피했습니다.",
            "신호 적중률은 같은 시작월의 관측 가능 시군구 시장평균과 함께 봐야 합니다.",
            "평가수는 지역×시그널 구간 행 수이며, 독립 표본 수나 투자 성과 보장이 아닙니다.",
        ],
    }
