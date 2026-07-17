"""근거기반 부동산 자문 에이전트 — Claude tool-use 루프.

챗봇은 시장 데이터를 지어내지 않고, 아래 tool 로 우리 API/데이터를 조회한 결과만 근거로 답한다.
예측은 백테스트 적중률(확률) 범위에서만, 개별 매수·매도 단정 금지(정보제공·의사결정 보조).
ANTHROPIC_API_KEY 미설정·SDK 미설치 시 available()=False → 엔드포인트가 안내 폴백.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("realty_signal")

OPUS = "claude-opus-4-8"
SONNET = "claude-sonnet-4-6"

SYSTEM = (
    "당신은 한국 부동산 의사결정을 돕는 데이터 애널리스트 'Nick'입니다. "
    "Signal APT 앱이 수집·산출한 데이터(지역 시그널, 국면, 실거래, 급매·경매·청약·재건축, 공시가격, 뉴스, 백테스트 적중률)를 근거로 답합니다.\n"
    "\n원칙(반드시 준수):\n"
    "1. 근거 없는 단정 금지. 수치·판단이 필요하면 반드시 제공된 tool 로 조회한 결과만 사용한다. 조회로 확인되지 않으면 '데이터에 없다'고 솔직히 말한다.\n"
    "2. 미래 가격을 확신형으로 예측하지 않는다. 방향성은 get_backtest 의 적중률(과거 확률)과 현재 국면·시그널 근거로 '확률·조건부'로만 말한다. '오른다/사라'가 아니라 '이 국면에서 역사적으로 상승 확률 X%'.\n"
    "3. 개별 단지의 매수·매도를 지시하지 않는다. 정보 제공·의사결정 보조이며, 최종 판단·실거래 확인은 이용자 책임임을 필요 시 덧붙인다. (유사투자자문 아님)\n"
    "4. 한국어로 간결하고 구체적으로. 표·불릿 남발 없이 핵심부터. 근거가 된 데이터의 기준일이 오래됐으면 그 점을 밝힌다.\n"
    "5. 여러 지표가 필요하면 tool 을 여러 번 호출해 종합한다. 사용자가 지역/단지를 명시하지 않으면 되묻거나 list_signal_regions 로 후보를 제시한다.\n"
    "6. 정책·규제·개발계획(대출규제·DSR·신도시·GTX·재건축 규제 등) 질문은 get_policy 로 지식베이스를 조회해 답하고, 결과의 출처(source)와 기준일(eff_date)을 함께 밝히며 '정책은 이후 변경됐을 수 있으니 최신 공고 확인'을 덧붙인다. 조회 결과가 없으면 모른다고 말한다(정책을 지어내지 말 것).\n"
    "7. 아래에 '사용자 프로필'이 주어지면 예산·거주지·관심지역을 우선 고려한다. 프로필에 없는 사실은 지어내지 말고, 예산 밖 후보를 말할 때는 예산 초과임을 명시한다.\n"
    "8. 지역 시그널(KB 주간 룰)과 단지 시그널(실거래·전세가율·공시)은 계층이 다르다. 단지 전세가율·갭·평단가는 get_complex 결과만 인용하고, 필드가 없거나 '데이터없음필드'면 추정하지 않는다. 지역 BUY를 단지 BUY로 바꿔 말하지 말 것.\n"
    "9. '지금 타이밍?'·'언제 사?' 류는 get_timing 으로 타이밍점수(0~100)·근거·confidence·asof 를 조회해 조건부로 답한다. 확신형 매수 지시 금지.\n"
)


def _fmt_manwon(v) -> str | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n >= 10000:
        return f"{n/10000:.1f}억"
    return f"{int(n):,}만"


def build_system(profile: dict | None = None, favorites: dict | None = None) -> str:
    """SYSTEM + 사용자 프로필/관심목록 컨텍스트. 비어 있으면 SYSTEM만."""
    bits = []
    p = profile or {}
    if p.get("가용자본"):
        bits.append(f"가용자본 {_fmt_manwon(p['가용자본'])}")
    if p.get("연소득"):
        bits.append(f"연소득 {_fmt_manwon(p['연소득'])}")
    if p.get("주택수") is not None and p.get("주택수") != "":
        bits.append(f"주택수 {p['주택수']}")
    if p.get("거주지"):
        bits.append(f"거주지 {p['거주지']}")
    if p.get("관심평수"):
        sizes = p["관심평수"] if isinstance(p["관심평수"], list) else [p["관심평수"]]
        bits.append("관심평수 " + ",".join(str(s) for s in sizes if s is not None))
    if p.get("청약관심"):
        bits.append("청약 관심 있음")
    fav = favorites or {}
    regs = fav.get("관심지역") or []
    cxs = fav.get("관심단지") or []
    if regs:
        bits.append("관심지역 " + ", ".join(regs[:12]))
    if cxs:
        bits.append("관심단지 " + ", ".join(cxs[:12]))
    if not bits:
        return SYSTEM
    return SYSTEM + "\n사용자 프로필(답변 시 참고):\n- " + "\n- ".join(bits) + "\n"


# Anthropic tool 스키마 — 각 tool 은 api.py 의 내부 데이터 함수에 매핑된다(server-side 실행).
TOOLS = [
    {
        "name": "list_signal_regions",
        "description": "매수/관망/매도주의 시그널이 강한 지역 목록을 시그널 순으로 반환. '어디가 좋아?' 같은 광역 질문에 사용.",
        "input_schema": {"type": "object", "properties": {
            "signal": {"type": "string", "enum": ["STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "SELL_RISK"],
                       "description": "특정 시그널만 필터(생략 시 전체)"},
            "limit": {"type": "integer", "description": "최대 개수(기본 15)"},
        }},
    },
    {
        "name": "get_region_signal",
        "description": "특정 지역(구/시군구)의 시그널과 근거 지표(전세수급·매수우위·매매모멘텀·공급압력·급지·저평가)와 해설을 반환.",
        "input_schema": {"type": "object", "properties": {
            "region": {"type": "string", "description": "지역명(예: '강남구', '성남시 분당구')"},
        }, "required": ["region"]},
    },
    {
        "name": "get_complex",
        "description": "특정 단지의 국토부 실거래 요약(평단가·2년 추세·전세가율·갭·단지 시그널·공시대비)을 반환.",
        "input_schema": {"type": "object", "properties": {
            "region": {"type": "string", "description": "단지가 속한 시군구"},
            "name": {"type": "string", "description": "단지명"},
        }, "required": ["region", "name"]},
    },
    {
        "name": "get_backtest",
        "description": "시그널 엔진의 과거 적중률(매수/매도 시그널이 이후 실제 가격 방향과 맞은 비율). 방향성·예측을 확률로 말할 때 근거로 사용.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_timing",
        "description": "지역·매물 타이밍 점수(0~100)와 근거·신뢰도(confidence)·기준일(asof). '지금 타이밍?'·'이 동네/매물 괜찮아?' 에 사용.",
        "input_schema": {"type": "object", "properties": {
            "layer": {"type": "string", "enum": ["region", "listing"],
                      "description": "region=지역 KB 시그널, listing=경매·급매 등 매물"},
            "region": {"type": "string", "description": "지역명(필수)"},
            "kind": {"type": "string", "enum": ["경매", "급매", "청약", "재건축"],
                     "description": "layer=listing 일 때 유형(기본 급매)"},
        }, "required": ["region"]},
    },
    {
        "name": "get_strength",
        "description": "시장강도 프록시(0~100)·라벨·거래량비·급매건수. 부동산지인/아실 대체. '거래 활발해?'·'유동성?' 류에 사용.",
        "input_schema": {"type": "object", "properties": {
            "region": {"type": "string", "description": "지역명(생략 시 상위 강도 지역)"},
        }},
    },
    {
        "name": "get_regime",
        "description": "현재 수도권 경기 국면(벌집순환/급지역전 등)과 β·급지갭 등 거시 상태를 반환.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_news",
        "description": "최근 부동산 뉴스 AI 요약(정책·금리·규제 흐름). topic 지정 가능.",
        "input_schema": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "주제 키워드(생략 가능)"},
        }},
    },
    {
        "name": "get_freshness",
        "description": "각 데이터 소스가 마지막으로 갱신된 시점과 분석 기준일. 답변의 신선도를 밝힐 때 사용.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_regulation",
        "description": "규제지역(투기과열지구·조정대상지역·토지거래허가구역) 지정 현황. region 지정 시 해당 지역, 생략 시 전체. 세제·대출·거래 규제와 직결되므로 지역/단지 매수 상담 시 확인. 지정/해제는 수시 변경되니 기준일을 밝힌다.",
        "input_schema": {"type": "object", "properties": {
            "region": {"type": "string", "description": "지역명(생략 시 전체 규제지역)"},
        }},
    },
    {
        "name": "get_presale",
        "description": "청약(분양) 단지 현황 — 접수중·예정, D-day, 지역 시그널·급지. region 지정 시 해당 지역. '청약 뭐 있어?' 류에 사용.",
        "input_schema": {"type": "object", "properties": {
            "region": {"type": "string", "description": "지역명(생략 시 전체, D-day 임박순)"},
        }},
    },
    {
        "name": "get_redev",
        "description": "지역 재건축 잠재력 단지(연식·용적률·세대수·시세 기반 후보). region 필수. '○○ 재건축 어디 유망?' 류에 사용. 데이터 미준비 지역은 그 사실을 안내.",
        "input_schema": {"type": "object", "properties": {
            "region": {"type": "string", "description": "지역명(시군구)"},
        }, "required": ["region"]},
    },
    {
        "name": "get_listings",
        "description": "실제 매물 — 급매(시세 이하 호가)·경매. region 으로 좁힘. kind: '급매'|'경매'|'전체'. '강남 급매 있어?' 류에 사용. 급매는 관리자 스캔 시점 기준.",
        "input_schema": {"type": "object", "properties": {
            "region": {"type": "string", "description": "지역명(선택)"},
            "kind": {"type": "string", "enum": ["급매", "경매", "전체"], "description": "매물 종류(기본 급매)"},
        }},
    },
    {
        "name": "get_policy",
        "description": "부동산 정책·규제·개발계획 지식베이스 검색(스트레스 DSR, 대출규제, 3기 신도시, GTX, 재건축 규제 등). "
                       "제도·개발계획 질문에 사용. 결과의 source·eff_date(시행/기준일)를 반드시 함께 인용하고, 정책은 변경될 수 있음을 밝힌다.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "검색어(정책명·키워드)"},
            "region": {"type": "string", "description": "관련 지역(선택)"},
        }, "required": ["query"]},
    },
]


def available() -> bool:
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _to_blocks(messages: list) -> list:
    """프론트의 {role, text} 히스토리 → anthropic messages(content=text)."""
    out = []
    for m in messages:
        role = "assistant" if m.get("role") == "assistant" else "user"
        text = (m.get("text") or m.get("content") or "").strip()
        if text:
            out.append({"role": role, "content": text})
    return out


def run_advisor(messages: list, tool_exec, model: str = SONNET, max_rounds: int = 6,
                system: str | None = None) -> dict:
    """tool-use 루프 실행. messages=[{role,text}...]. tool_exec(name, input)->dict.

    반환 {"answer": str, "used": [tool 이름들], "rounds": n}. 실패 시 answer=None.
    """
    if not available():
        return {"answer": None, "used": [], "rounds": 0}
    try:
        import anthropic
    except ImportError:
        return {"answer": None, "used": [], "rounds": 0}
    convo = _to_blocks(messages)
    if not convo:
        return {"answer": None, "used": [], "rounds": 0}
    sys_prompt = system or SYSTEM
    used: list[str] = []
    try:
        client = anthropic.Anthropic()
        for _ in range(max_rounds):
            resp = client.messages.create(
                model=model, max_tokens=1500, system=sys_prompt, tools=TOOLS, messages=convo,
            )
            if resp.stop_reason == "tool_use":
                convo.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    used.append(block.name)
                    try:
                        out = tool_exec(block.name, dict(block.input or {}))
                    except Exception as e:  # noqa: BLE001
                        log.warning("advisor tool %s 실패: %s", block.name, e)
                        out = {"error": "조회 실패"}
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(out, ensure_ascii=False, default=str)[:8000]})
                convo.append({"role": "user", "content": results})
                continue
            # 최종 답변
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            return {"answer": text or None, "used": sorted(set(used)), "rounds": _}
        # 라운드 소진 — 마지막 응답 텍스트라도
        return {"answer": None, "used": sorted(set(used)), "rounds": max_rounds}
    except Exception as e:  # noqa: BLE001
        log.warning("advisor 실패: %s", e)
        return {"answer": None, "used": sorted(set(used)), "rounds": 0}


def run_advisor_stream(messages: list, tool_exec, model: str = SONNET, max_rounds: int = 6,
                       system: str | None = None):
    """tool-use 루프를 스트리밍으로. 이벤트 dict 를 yield:
       {"type":"status","tool":name} · {"type":"delta","text":...} · {"type":"done","used":[...]} · {"type":"error"}.
    """
    if not available():
        yield {"type": "error", "message": "no_ai"}
        return
    try:
        import anthropic
    except ImportError:
        yield {"type": "error", "message": "no_ai"}
        return
    convo = _to_blocks(messages)
    if not convo:
        yield {"type": "error", "message": "empty"}
        return
    sys_prompt = system or SYSTEM
    used: list[str] = []
    try:
        client = anthropic.Anthropic()
        for _ in range(max_rounds):
            with client.messages.stream(
                model=model, max_tokens=1500, system=sys_prompt, tools=TOOLS, messages=convo,
            ) as stream:
                for chunk in stream.text_stream:
                    if chunk:
                        yield {"type": "delta", "text": chunk}
                final = stream.get_final_message()
            if final.stop_reason == "tool_use":
                convo.append({"role": "assistant", "content": final.content})
                results = []
                for block in final.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    used.append(block.name)
                    yield {"type": "status", "tool": block.name}
                    try:
                        out = tool_exec(block.name, dict(block.input or {}))
                    except Exception as e:  # noqa: BLE001
                        log.warning("advisor(stream) tool %s 실패: %s", block.name, e)
                        out = {"error": "조회 실패"}
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(out, ensure_ascii=False, default=str)[:8000]})
                convo.append({"role": "user", "content": results})
                continue
            yield {"type": "done", "used": sorted(set(used))}
            return
        yield {"type": "done", "used": sorted(set(used))}
    except Exception as e:  # noqa: BLE001
        log.warning("advisor(stream) 실패: %s", e)
        yield {"type": "error", "message": "failed", "used": sorted(set(used))}
