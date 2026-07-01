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
