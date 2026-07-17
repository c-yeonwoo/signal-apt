"""Nick advisor · memory."""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse

from realty_signal import config, db
from realty_signal.routes import deps
from realty_signal.services import market_data as md

router = APIRouter(tags=["advisor"])


@router.get("/api/advisor/memory")
def advisor_memory_get(request: Request):
    from realty_signal.brain import memory as nick_mem
    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    return {"ok": True, "memory": nick_mem.to_public(nick_mem.load(uid))}


@router.delete("/api/advisor/memory")
def advisor_memory_clear(request: Request):
    from realty_signal.brain import memory as nick_mem
    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    nick_mem.clear(uid)
    return {"ok": True}


@router.post("/api/advisor")
def advisor_api(request: Request, data: dict = Body(...)):
    from realty_signal import advisor
    from realty_signal import api as app_api
    config.load_env()
    uid = deps.uid(request)
    if not uid:
        return {"ok": False, "reason": "login_required"}
    if not advisor.available():
        return {"ok": False, "reason": "no_ai",
                "answer": "AI 자문은 서버에 ANTHROPIC_API_KEY 가 설정되어야 이용할 수 있습니다."}
    unlimited = deps.is_opus_user(request) or deps.is_admin(request)
    ok, ust = deps.usage_allow(uid, "nick", unlimited=unlimited)
    if not ok:
        return {"ok": False, "reason": "limit", "usage": ust,
                "answer": f"이번 주 Nick 질문 한도({ust['limit']}회)에 도달했습니다. "
                          "관심지역 추적·동네 리포트·시그널은 계속 이용할 수 있습니다."}
    messages = data.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return {"ok": False, "reason": "empty"}
    messages = messages[-12:]
    model = advisor.OPUS if deps.is_opus_user(request) else advisor.SONNET
    system = app_api._nick_system(uid)
    app_api._ADVISOR_UID = uid
    try:
        res = advisor.run_advisor(messages, app_api._advisor_tool, model=model, system=system)
    finally:
        app_api._ADVISOR_UID = None
    if not res.get("answer"):
        return {"ok": False, "reason": "failed",
                "answer": "지금은 답변을 생성하지 못했습니다. 질문을 조금 더 구체적으로(지역·단지) 주시면 도움이 됩니다."}
    db.usage_inc(uid, "nick")
    app_api._nick_remember(uid, messages, res.get("answer"))
    ust = deps.usage_status(uid, "nick", unlimited=unlimited)
    return {"ok": True, "answer": res["answer"], "used": res.get("used", []),
            "기준일": str(md.kb().last_date.date()), "usage": ust}


@router.post("/api/advisor/stream")
def advisor_stream_api(request: Request, data: dict = Body(...)):
    from realty_signal import advisor
    from realty_signal import api as app_api
    config.load_env()
    uid = deps.uid(request)
    asof = str(md.kb().last_date.date())
    opus = deps.is_opus_user(request)
    unlimited = opus or deps.is_admin(request)
    messages = (data.get("messages") or [])[-12:]
    system = app_api._nick_system(uid)
    answer_buf: list[str] = []

    def _one(ev: dict) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def gen():
        if not uid:
            yield _one({"type": "error", "message": "login_required"}); return
        if not advisor.available():
            yield _one({"type": "error", "message": "no_ai"}); return
        if not messages:
            yield _one({"type": "error", "message": "empty"}); return
        ok, ust = deps.usage_allow(uid, "nick", unlimited=unlimited)
        if not ok:
            yield _one({"type": "error", "message": "limit", "usage": ust}); return
        model = advisor.OPUS if opus else advisor.SONNET
        app_api._ADVISOR_UID = uid
        try:
            for ev in advisor.run_advisor_stream(messages, app_api._advisor_tool, model=model, system=system):
                if ev.get("type") == "delta" and ev.get("text"):
                    answer_buf.append(ev["text"])
                if ev.get("type") == "done":
                    db.usage_inc(uid, "nick")
                    app_api._nick_remember(uid, messages, "".join(answer_buf) or None)
                    ev["기준일"] = asof
                    ev["usage"] = deps.usage_status(uid, "nick", unlimited=unlimited)
                yield _one(ev)
        except Exception:  # noqa: BLE001
            yield _one({"type": "error", "message": "failed"})
        finally:
            app_api._ADVISOR_UID = None

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
