"""부동산 뉴스 KB — 네이버 뉴스 검색 API로 토픽별 최신 뉴스 수집(크롤링 아님, 공식 API).

토픽 태깅 + link 기준 누적(db.news) → 최신 피드 + 추후 AI 리포트의 정책·시장 맥락 소스.
저작권: 제목·요약 스니펫 + 원문 링크만(원문 복제 X).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("realty_signal")
MODEL = "claude-opus-4-8"

# 토픽 → 검색어
_TOPICS = {
    "정책": "부동산 정책",
    "금리": "부동산 대출 금리",
    "청약": "아파트 청약",
    "재건축": "재건축 재개발",
    "시장": "부동산 시장 시세",
}


def _strip(s: str) -> str:
    s = re.sub(r"</?b>", "", s or "")
    return (s.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&#39;", "'").strip())


def _source(originallink: str, link: str) -> str:
    host = urllib.parse.urlparse(originallink or link or "").netloc.replace("www.", "")
    return host.split(".")[0] if host else "news"


def _naver_news(query: str, cid: str, csec: str, n: int = 10) -> list[dict]:
    url = "https://openapi.naver.com/v1/search/news.json?" + urllib.parse.urlencode(
        {"query": query, "display": n, "sort": "date"})
    req = urllib.request.Request(url, headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())  # noqa: S310
    except Exception as e:
        log.warning("naver news 실패(%s): %s", query, e)
        return []
    return data.get("items", [])


_SUM_SYS = (
    "당신은 부동산 뉴스 큐레이터입니다. 최근 뉴스 제목·요약 묶음을 받아, 부동산 매수·투자자가 "
    "꼭 알아야 할 핵심을 정리합니다.\n"
    "- 마크다운으로 딱 두 섹션: ## 최근 주요 이슈 (3~5개 불릿) / ## 꼭 알아야 할 변경점 (2~4개 불릿, 정책·금리·제도 변화 중심).\n"
    "- 각 불릿 한 줄, 구체적으로. 개별 기사 광고·홍보성 내용은 제외. 확정 아닌 건 '~전망/논의'로.\n"
    "- 뉴스에 근거해서만. 없으면 억지로 만들지 말 것. 전체 400~600자. 마지막에 '※ 뉴스 헤드라인 기반 요약' 한 줄."
)

_SUM_SYS_DETAIL = (
    "당신은 부동산 뉴스 애널리스트입니다. 특정 테마의 최근 뉴스 묶음을 받아, 그 테마를 깊이 있게 정리합니다.\n"
    "- 마크다운 세 섹션: ## 핵심 이슈 (4~6개 불릿, 수치·지역·단지 등 구체적으로) / "
    "## 제도·정책 변경점 (있는 경우 2~4개 불릿) / ## 매수자 관점 시사점 (2~3개 불릿).\n"
    "- 각 불릿 한 줄. 광고·홍보성 제외. 확정 아닌 건 '~전망/논의'. 뉴스 근거 내에서만.\n"
    "- 전체 600~900자. 마지막에 '※ 뉴스 헤드라인 기반 분석' 한 줄."
)


def summarize(topic: str | None, items: list[dict], detail: bool = False) -> str | None:
    """최근 뉴스 묶음 → Claude 요약. detail=True(특정 테마)면 심층 3섹션. 키/항목 부족 시 None."""
    try:
        import anthropic
    except ImportError:
        return None
    corpus = "\n".join(f"- [{i.get('topic')}] {i.get('title')} — {(i.get('descr') or '')[:80]}" for i in items)
    if not corpus.strip():
        return None
    scope = f"'{topic}' 테마" if (topic and topic != "전체") else "부동산 전반"
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL, max_tokens=1600 if detail else 1200,
            system=_SUM_SYS_DETAIL if detail else _SUM_SYS,
            messages=[{"role": "user", "content": f"{scope} 최근 뉴스 {len(items)}건입니다. 요약하세요.\n\n{corpus[:12000]}"}])
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except Exception as e:
        log.warning("뉴스 요약 실패: %s", e)
        return None


_CHANGE_KW = ("정책", "규제", "대출", "금리", "세제", "세금", "완화", "지정", "해제", "공급", "제도")


def mock_summary(topic: str | None, items: list[dict], detail: bool = False) -> str:
    """로컬 dev 용 목업 요약 — LLM 호출 없이 실제 헤드라인으로 그럴듯한 형태만 구성.

    실제 AI 분석이 아니라 개발 중 UI 확인용. prod 에서는 summarize() 사용.
    """
    titles = [i.get("title", "").strip() for i in items if i.get("title")]
    top = titles[:6 if detail else 5]
    changes = [t for t in titles if any(k in t for k in _CHANGE_KW)][:4]
    scope = f"'{topic}'" if (topic and topic != "전체") else "부동산 전반"
    if detail:
        parts = [f"## 핵심 이슈", *[f"- {t}" for t in top[:6]]]
        if changes:
            parts += ["", "## 제도·정책 변경점", *[f"- {t}" for t in changes]]
        parts += ["", "## 매수자 관점 시사점",
                  f"- {scope} 관련 헤드라인이 최근 {len(items)}건 수집됨 — 흐름 모니터링 권장.",
                  "- 규제·금리 변화는 실거래에 시차를 두고 반영되는 경향."]
        parts.append("\n※ 로컬 목업 요약 (실제 AI 분석은 배포 환경에서 생성)")
        return "\n".join(parts)
    parts = [f"## 최근 주요 이슈", *[f"- {t}" for t in top]]
    if changes:
        parts += ["", "## 꼭 알아야 할 변경점", *[f"- {t}" for t in changes]]
    parts.append("\n※ 로컬 목업 요약 (실제 AI 분석은 배포 환경에서 생성)")
    return "\n".join(parts)


def fetch_news(cid: str, csec: str, per_topic: int = 12) -> list[dict]:
    """토픽별 최신 뉴스 수집 → 정규화·태깅. link 기준 dedupe."""
    seen: dict[str, dict] = {}
    for topic, q in _TOPICS.items():
        for it in _naver_news(q, cid, csec, per_topic):
            link = it.get("link") or it.get("originallink")
            if not link or link in seen:
                continue
            seen[link] = {
                "link": link, "title": _strip(it.get("title", "")),
                "descr": _strip(it.get("description", "")),
                "source": _source(it.get("originallink", ""), link),
                "topic": topic, "pubdate": (it.get("pubDate") or "")[:16],
            }
    return list(seen.values())
