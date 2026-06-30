"""임장 리포트 — 유튜브·블로그 임장 콘텐츠를 수집해 단지별 종합 리포트로 가공.

3단계 폴백:
  links     키 없음           → 유튜브/네이버 검색 딥링크만
  curation  YouTube/Naver 키  → 영상·블로그 결과 카드
  report    + ANTHROPIC 키    → 자막·블로그를 Claude로 항목별 종합 리포트(+출처)

저작권: 원문 복제 금지. 변형 요약 + 출처 링크 병기. (유료 제품 보수적 운용)
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger("realty_signal")
MODEL = "claude-opus-4-8"
_UA = {"User-Agent": "Mozilla/5.0"}


def _search_links(name: str) -> dict:
    q = urllib.parse.quote(f"{name} 임장")
    return {"youtube": f"https://www.youtube.com/results?search_query={q}",
            "naver": f"https://search.naver.com/search.naver?where=blog&query={q}"}


def youtube_search(query: str, key: str, n: int = 4) -> list[dict]:
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode({
        "part": "snippet", "q": query, "type": "video", "maxResults": n,
        "relevanceLanguage": "ko", "regionCode": "KR", "key": key})
    try:
        data = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=10).read())  # noqa: S310
    except Exception as e:
        log.warning("youtube search 실패: %s", e)
        return []
    out = []
    for it in data.get("items", []):
        vid = (it.get("id") or {}).get("videoId")
        sn = it.get("snippet") or {}
        if not vid:
            continue
        out.append({"videoId": vid, "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                    "thumb": (((sn.get("thumbnails") or {}).get("medium") or {}).get("url")),
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


def transcript(video_id: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        segs = YouTubeTranscriptApi.get_transcript(video_id, languages=["ko", "en"])
        return " ".join(s["text"] for s in segs)[:6000]
    except Exception:
        return ""


def naver_blog(query: str, cid: str, csec: str, n: int = 4) -> list[dict]:
    url = "https://openapi.naver.com/v1/search/blog.json?" + urllib.parse.urlencode(
        {"query": query, "display": n, "sort": "sim"})
    req = urllib.request.Request(url, headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())  # noqa: S310
    except Exception as e:
        log.warning("naver blog 실패: %s", e)
        return []

    def _clean(s):
        return (s or "").replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&")
    return [{"title": _clean(it.get("title")), "desc": _clean(it.get("description")),
             "blogger": it.get("bloggername", ""), "date": it.get("postdate", ""), "url": it.get("link")}
            for it in data.get("items", [])]


_SYS = (
    "당신은 부동산 임장(현장답사) 정보를 종합하는 애널리스트입니다. 여러 유튜브 자막과 블로그 후기를 "
    "받아, 한 아파트 단지의 임장 리포트를 사용자 관점에서 객관적으로 정리합니다.\n"
    "- 원문을 그대로 베끼지 말고 핵심만 변형·요약. 과장·확정 표현 금지, 후기 기반임을 전제.\n"
    "- 마크다운으로 다음 항목을 간결히: ## 한줄 총평 / ## 교통·접근성 / ## 학군·교육 / "
    "## 주거환경(소음·조망·관리·편의) / ## 단점·유의점 / ## 매수 관점.\n"
    "- 정보가 없는 항목은 '후기에서 언급 적음'으로. 전체 500~700자. 마지막에 '※ 외부 후기 종합, 현장 확인 필요' 한 줄."
)


def synthesize(name: str, transcripts: list[str], blogs: list[dict]) -> str | None:
    try:
        import anthropic
    except ImportError:
        return None
    corpus = "\n\n".join(f"[유튜브 자막 {i+1}]\n{t}" for i, t in enumerate(transcripts) if t)
    corpus += "\n\n" + "\n\n".join(f"[블로그] {b['title']}\n{b['desc']}" for b in blogs)
    if not corpus.strip():
        return None
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL, max_tokens=1500, system=_SYS,
            messages=[{"role": "user", "content": f"단지: {name}\n\n다음 외부 후기들을 종합해 임장 리포트를 작성하세요.\n\n{corpus[:18000]}"}])
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except Exception as e:
        log.warning("임장 리포트 생성 실패: %s", e)
        return None


def build_report(name: str, *, yt_key=None, nv_id=None, nv_sec=None, anthropic_on=False) -> dict:
    """단지 임장 리포트 — 가능한 최고 tier로. links < curation < report."""
    q = f"{name} 임장"
    res = {"단지명": name, "tier": "links", "search": _search_links(name), "videos": [], "blogs": [], "report": None}
    videos = youtube_search(q, yt_key) if yt_key else []
    blogs = naver_blog(q, nv_id, nv_sec) if (nv_id and nv_sec) else []
    res["videos"], res["blogs"] = videos, blogs
    if videos or blogs:
        res["tier"] = "curation"
    if anthropic_on and (videos or blogs):
        trs = [transcript(v["videoId"]) for v in videos[:3]]
        rep = synthesize(name, trs, blogs)
        if rep:
            res["report"], res["tier"] = rep, "report"
    return res
