"""매물 목록 공통 컴포넌트의 열기·닫기 UX 계약."""

from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "src/realty_signal/web/index.html").read_text(encoding="utf-8")


def test_listing_detail_rows_toggle_and_expose_expanded_state():
    """같은 행을 두 번 누르면 닫혀야 한다 — 급매·경매·통합 목록 공통 동작."""
    block = HTML[HTML.index("async function selectMsRow"):HTML.index("async function mapSplit")]
    assert "const isOpen=" in block
    assert "if(isOpen){" in block
    assert "det.style.display='none'" in block
    assert "row.setAttribute('aria-expanded','false')" in block
    assert "row.setAttribute('aria-expanded','true')" in block


def test_listing_detail_rows_are_keyboard_operable():
    block = HTML[HTML.index("function renderMsList"):HTML.index("function applyViewportFilter")]
    assert "row.setAttribute('role','button')" in block
    assert "row.tabIndex=0" in block
    assert "e.key==='Enter'||e.key===' '" in block
