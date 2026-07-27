"""결론 탭 랭킹 — 예산(레버리지) 안에서는 예산을 쓰는 쪽을 우대."""

from realty_signal.services import shortlist as sl


def _score(signal_rank: int, est: float, budget: float, uv: float, loc: float = 0):
    """api.conclusion 과 같은 식."""
    affordable = est <= budget
    ratio = (est / budget) if (affordable and budget) else 0.0
    budget_fit = sl._budget_score(ratio) if affordable else 0.0
    return affordable, signal_rank * 10000 + budget_fit * 50 + uv * 10 + loc


def test_prefers_near_budget_over_cheap_undervalued():
    # 같은 BUY: 예산 97%·저평가 37 vs 예산 68%·저평가 63 → 전자 승
    a = _score(1, 39_000, 40_000, uv=37)  # 권선구 류
    b = _score(1, 27_000, 40_000, uv=63)  # 의정부 류
    assert a[0] and b[0]
    assert a[1] > b[1]


def test_over_budget_loses_to_in_budget():
    over = _score(2, 49_000, 40_000, uv=80)  # STRONG_BUY지만 초과
    inn = _score(1, 39_000, 40_000, uv=37)
    assert (inn[0], inn[1]) > (over[0], over[1])
