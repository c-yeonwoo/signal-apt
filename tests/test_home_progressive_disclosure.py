"""홈 첫 화면은 초보 구매자의 다음 행동을 먼저 보여야 한다."""

from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "src/realty_signal/web/index.html").read_text(encoding="utf-8")


def _dashboard_markup() -> str:
    start = HTML.index('body.innerHTML=`<div id="dashComeback"')
    return HTML[start:HTML.index('  _loadBuyingPower();', start)]


def test_dashboard_keeps_primary_actions_before_optional_cards():
    markup = _dashboard_markup()
    plan = markup.index('id="dashPlanWrap"')
    power = markup.index('id="dashPowerWrap"')
    budget = markup.index('id="dashBudgetNewWrap"')
    extra = markup.index('id="dashExtra"')
    assert plan < power < budget < extra
    assert '관심 동네·후보 더 보기' in markup


def test_dashboard_hides_market_evidence_behind_progressive_disclosure():
    markup = _dashboard_markup()
    weekly = markup.index('id="dashWeeklyWrap"')
    extra = markup.index('id="dashMarketExtra"')
    near = markup.index('id="dashNearWrap"')
    assert weekly < extra < near
    assert '시장 근거·이슈 더 보기' in markup
