"""문서와 코드가 갈라지는 것을 막는다.

2026-08-17 진단의 1번 발견이 **문서-코드 드리프트**였다:
- `README.md`·`CLAUDE.md` 가 매수 트리거를 실제와 **정반대**로 적어 뒀고,
  그걸 믿고 만든 `brain/calibrate.py` 가 등급식에 없는 파라미터를 스윕했다.
- `DESIGN.md` 토큰 표가 실제 `:root` 보다 낡아 색 12개 중 11개가 달랐다.

사람이 문서를 갱신하기를 바라는 것으로는 다시 갈라진다. 그래서 테스트로 고정한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "src" / "realty_signal" / "web" / "index.html"
DESIGN = REPO / "DESIGN.md"
README = REPO / "README.md"
CLAUDE = REPO / "CLAUDE.md"

# DESIGN.md §2 가 정본으로 옮겨 적어야 하는 토큰
_TRACKED = [
    "--bg", "--panel", "--line", "--txt", "--dim",
    "--accent", "--accent-ink",
    "--sig-strong", "--sig-buy", "--sig-watch", "--sig-neutral", "--sig-sell",
    "--quick", "--success", "--danger", "--warn",
]


def _root_tokens() -> dict[str, str]:
    """`index.html` 의 `:root` 에서 토큰 실측값을 뽑는다."""
    css = re.search(r":root \{(.*?)\n  \}", INDEX.read_text(encoding="utf-8"), re.S)
    assert css, ":root 블록을 찾지 못했다"
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"(--[a-z-]+)\s*:\s*([^;]+);", css.group(1))
    }


def _design_tokens() -> dict[str, str]:
    """`DESIGN.md` §2 코드블록에 적힌 값."""
    body = DESIGN.read_text(encoding="utf-8")
    block = re.search(r"## 2\. 토큰.*?```(.*?)```", body, re.S)
    assert block, "DESIGN.md §2 토큰 코드블록을 찾지 못했다"
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"(--[a-z-]+)\s+(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))", block.group(1))
    }


@pytest.mark.parametrize("token", _TRACKED)
def test_design_md_matches_root(token):
    """DESIGN.md §2 의 색 값이 실제 `:root` 와 같아야 한다.

    어긋나면 **문서를 코드에 맞춰 고친다.** 반대로 하지 마라 —
    코드는 접근성 때문에 어두운 팔레트로 옮겨 왔고, 되돌리면 대비비가 퇴행한다.
    """
    root, doc = _root_tokens(), _design_tokens()
    assert token in root, f"{token} 이 :root 에 없다"
    assert token in doc, f"{token} 이 DESIGN.md §2 표에 없다 — 문서를 갱신하라"
    assert doc[token].lower() == root[token].lower(), (
        f"{token}: DESIGN.md={doc[token]} vs :root={root[token]} — "
        "문서를 코드에 맞춰 갱신하라"
    )


def test_docs_do_not_claim_demand_ladder_is_the_trigger():
    """매수 트리거는 **매수우위지수**(`buyer_superiority`)다. 매수세우위 사다리가 아니다.

    `engine.py:_classify` 는 `sum([crunch, idx_strong, rising])` 로 등급을 매기고,
    `buyer_demand` 는 `[참고]` 근거 문자열에만 쓴다. 문서가 반대로 적히면
    그 문서를 믿고 만든 도구(calibrate 등)까지 함께 틀어진다.
    """
    from realty_signal.signals.engine import _classify  # noqa: F401
    import inspect

    from realty_signal.signals import engine

    src = inspect.getsource(engine._classify)
    # 코드 사실 확인 — 등급은 chart_bull 로 정해지고 bd 는 등급식에 없다
    assert "chart_bull = sum([crunch, idx_strong, rising])" in src
    grade_block = src.split("chart_bull = sum")[1]
    assert "bd" not in grade_block.split("return")[0].replace("buyer_demand", ""), (
        "매수세우위(bd)가 등급식에 들어왔다면 문서와 이 테스트를 함께 갱신하라"
    )

    for path in (README, CLAUDE):
        text = path.read_text(encoding="utf-8")
        # 옛 오류 문구가 되살아나지 않았는지
        assert "차트용 **참고값**(별개), 시그널 트리거 아님" not in text, (
            f"{path.name}: 매수우위지수를 '트리거 아님'이라고 적은 옛 서술이 되살아났다"
        )
        assert '사다리가 적용되는 값. 시그널 트리거.' not in text, (
            f"{path.name}: 매수세우위를 '시그널 트리거'라고 적은 옛 서술이 되살아났다"
        )


def test_claude_md_describes_home_as_two_columns():
    """CLAUDE.md 의 홈 서술이 실제 2열 레이아웃과 맞아야 한다."""
    html = INDEX.read_text(encoding="utf-8")
    assert "dash-col-me" in html and "dash-col-market" in html, "홈이 2열 구조가 아니다"
    text = CLAUDE.read_text(encoding="utf-8")
    assert "상단 3장이 주간 축" not in text, "홈을 '3장 세로'로 적은 옛 서술이 되살아났다"
    assert "2열" in text, "CLAUDE.md 가 홈 2열 구조를 설명하지 않는다"
