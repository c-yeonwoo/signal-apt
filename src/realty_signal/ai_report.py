"""AI 심층 리포트 — Claude로 프로필+종합데이터 기반 개인화 매수전략을 생성.

ANTHROPIC_API_KEY 미설정/SDK 미설치/호출 실패 시 None 반환 → 프론트가 규칙기반 폴백.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("realty_signal")

MODEL = "claude-opus-4-8"

_SYSTEM = (
    "당신은 한국 부동산 매수 전략 애널리스트입니다. 사용자의 프로필(주택수·가용자본·거주지·"
    "관심평수·청약가점)과 데이터 분석 결과(매수 시그널 지역, 저평가도, 예상 매수가, 급매·청약·"
    "재건축 현황)를 바탕으로, 그 사람에게 맞는 매수 전략을 제시합니다.\n"
    "- 한국어로, 신뢰감 있고 구체적으로. 데이터에 근거해 단정적으로 말하되 과장 금지.\n"
    "- 구조: ①한줄 요약 ②추천 지역 2~3곳과 이유 ③거주지·청약 관점 ④최근 정책·시장 뉴스 반영 ⑤리스크/유의점 ⑥다음 행동.\n"
    "- '최근 뉴스'가 주어지면 그 정책·규제·금리 흐름을 전략에 반드시 반영(예: 규제지역 지정, 대출 규제, 금리 방향).\n"
    "- 마크다운 헤더(##)와 굵게(**)를 적절히 사용. 700자 내외로 핵심만.\n"
    "- 투자 권유가 아닌 데이터 해석임을 마지막에 한 줄로 고지."
)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def generate(profile: dict, summary: dict, news: list | None = None) -> str | None:
    """프로필 + 결론 요약 (+최근 뉴스 맥락) → Claude 심층 리포트(markdown). 불가 시 None."""
    if not available():
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK 미설치 — AI 리포트 폴백")
        return None
    payload = {"프로필": profile, "분석결과": summary}
    if news:
        payload["최근뉴스"] = news
    user = ("아래 JSON은 한 사용자의 프로필과 부동산 데이터 분석 결과입니다. "
            "이 사람을 위한 개인화 매수 전략 리포트를 작성하세요.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except Exception as e:  # 키 오류·레이트리밋·네트워크 → 폴백
        log.warning("AI 리포트 생성 실패: %s", e)
        return None
