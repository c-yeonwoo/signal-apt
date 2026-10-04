"""홈 첫 화면은 초보 구매자의 다음 행동을 먼저 보여야 한다."""

from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "src/realty_signal/web/index.html").read_text(encoding="utf-8")


def _dashboard_markup() -> str:
    start = HTML.index('body.innerHTML=`<div id="dashComeback"')
    return HTML[start:HTML.index('// ===== 이번 주 변화', start)]


def test_dashboard_prioritizes_favorites_and_market_changes():
    markup = _dashboard_markup()
    complex_watch = markup.index('id="dashCxWrap"')
    favorite_regions = markup.index('id="dashFavRegionWrap"')
    weekly = markup.index('id="dashWeeklyWrap"')
    plan = markup.index('id="dashPlanWrap"')
    assert favorite_regions < weekly < plan
    assert '관심단지' in markup
    assert '관심 지역과 주간 변화' in markup
    assert '지금 확인할 일' in markup
    assert 'id="dashExtra"' not in markup
    assert 'id="dashShortlistWrap"' not in markup
    assert 'id="dashLeversWrap"' not in markup
    assert 'id="dashBudgetNewWrap"' not in markup
    assert 'id="dashEstimateWrap"' not in markup
    assert '가격을 짐작한 단지' not in markup
    assert markup.index('_loadPlan();') > complex_watch
    assert markup.index('_loadComplexWatch();') > complex_watch


def test_dashboard_hides_market_evidence_behind_progressive_disclosure():
    markup = _dashboard_markup()
    weekly = markup.index('id="dashWeeklyWrap"')
    extra = markup.index('id="dashMarketExtra"')
    near = markup.index('id="dashNearWrap"')
    assert weekly < extra < near
    assert '시장 근거·이슈 더 보기' in markup
