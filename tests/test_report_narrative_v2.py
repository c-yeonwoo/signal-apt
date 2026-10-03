"""Optional paid explanations cannot change a deterministic report decision."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from realty_signal import db
from realty_signal.routes import reports_v2
from realty_signal.services import report_narrative as narrative


def _listing(report_id="a" * 64):
    return {"report_id": report_id, "type": "listing", "status": "ready",
            "subject": {"key": "일반매물:one", "kind": "일반매물", "name": "테스트 단지"},
            "lines": {"price": "호가와 실거래는 직접 비교 전입니다."},
            "price": {"이유": "동일 조건 거래가 부족합니다."},
            "positive": [], "cautions": [
                {"text": "동일 조건 거래가 부족합니다.", "evidence": "trades"}],
            "evidence": [{"id": "listing", "label": "수집 매물", "status": "수집 결과"},
                         {"id": "trades", "label": "국토부 실거래", "status": "보류"}]}


def test_explicit_queue_is_idempotent_private_and_does_not_call_model_on_get(monkeypatch):
    report = _listing()
    calls = []
    monkeypatch.setattr(narrative, "_generate", lambda *args: calls.append(args) or
                        narrative._validate_generated({"claims": [{"text": "비교 거래가 부족해 가격 판단은 보류해야 합니다.",
                                                                     "evidence_ids": ["trades"]}]},
                                                      report, "easy"))
    first, code = narrative.enqueue(7, report, "easy", "", private_source=True)
    assert code == 202 and first["status"] == "queued"
    repeated, code = narrative.enqueue(7, report, "easy", "", private_source=True)
    assert code == 202 and repeated["job_id"] == first["job_id"]
    assert narrative.get(8, first["job_id"], private_allowed=True) is None
    assert narrative.get(7, first["job_id"], private_allowed=False) is None
    assert narrative.get(7, first["job_id"], private_allowed=True)["status"] == "queued"
    assert calls == []
    assert narrative.run_once(private_user_allowed=lambda uid: uid == 7)
    complete = narrative.get(7, first["job_id"], private_allowed=True)
    assert complete["status"] == "succeeded"
    assert complete["result"]["report_id"] == report["report_id"]
    assert complete["result"]["source"] == "model_validated"
    assert complete["result"]["cautions"] == ["동일 조건 거래가 부족합니다."]
    assert len(calls) == 1
    c = db.conn()
    try:
        assert c.execute("SELECT question,report_data FROM report_explanation_jobs_v2 WHERE id=?",
                         (first["job_id"],)).fetchone() == ("", "")
    finally:
        c.close()
    cached, code = narrative.enqueue(7, report, "easy", "", private_source=True)
    assert code == 200 and cached == complete and len(calls) == 1
    changed, code = narrative.enqueue(7, _listing("b" * 64), "easy", "", private_source=True)
    assert code == 202 and changed["job_id"] != first["job_id"]


def test_bad_model_output_falls_back_and_never_exposes_unsupported_claim(monkeypatch):
    report = _listing()
    bad = [{"text": "내년 가격은 20% 오릅니다.", "evidence_ids": ["trades"]},
           {"text": "거래는 두 배 더 많아졌습니다.", "evidence_ids": ["trades"]},
           {"text": "중개사가 학교 배정을 보장합니다.", "evidence_ids": ["listing"]},
           {"text": "가격 판단을 보류하는 편이 안전합니다.", "evidence_ids": ["invented"]}]
    for claim in bad:
        with pytest.raises(ValueError):
            narrative._validate_generated({"claims": [claim]}, report, "easy")
    with pytest.raises(ValueError, match="counterevidence"):
        narrative._validate_generated({"claims": [{"text": "매물의 가격 근거가 충분합니다.",
                                                   "evidence_ids": ["trades"]}]}, report, "counterevidence")
    monkeypatch.setattr(narrative, "_generate", lambda *args: (_ for _ in ()).throw(
        ValueError("prompt injection or malformed model output")))
    queued, _ = narrative.enqueue(7, report, "easy", "", private_source=False)
    assert narrative.run_once()
    result = narrative.get(7, queued["job_id"], private_allowed=False)
    assert result["status"] == "failed"
    assert result["result"]["source"] == "deterministic_fallback"
    assert "prompt injection" not in json.dumps(result)


def test_expired_lease_fails_closed_without_second_paid_attempt(monkeypatch):
    report = _listing()
    queued, _ = narrative.enqueue(7, report, "counterevidence", "", private_source=False)
    row = narrative._claim()
    assert row[0] == queued["job_id"]
    c = db.conn()
    try:
        c.execute("UPDATE report_explanation_jobs_v2 SET lease_until=0 WHERE id=?", (row[0],))
        c.commit()
    finally:
        c.close()
    monkeypatch.setattr(narrative, "_generate", lambda *args: (_ for _ in ()).throw(
        AssertionError("must not charge twice")))
    assert narrative.run_once() is False
    result = narrative.get(7, row[0], private_allowed=False)
    assert result["status"] == "failed"
    assert result["result"]["source"] == "deterministic_fallback"


def test_private_permission_revoked_before_worker_skips_paid_call(monkeypatch):
    report = _listing()
    queued, _ = narrative.enqueue(7, report, "easy", "", private_source=True)
    monkeypatch.setattr(narrative, "_generate", lambda *args: (_ for _ in ()).throw(
        AssertionError("revoked source must not be sent to model")))
    assert narrative.run_once(private_user_allowed=lambda uid: False)
    assert narrative.get(7, queued["job_id"], private_allowed=False) is None
    result = narrative.get(7, queued["job_id"], private_allowed=True)
    assert result["status"] == "failed"
    assert result["result"]["source"] == "deterministic_fallback"


def test_generation_uses_metered_boundary_and_rejects_fake_evidence(monkeypatch):
    report = _listing()
    recorded = []

    class FakeMessages:
        def create(self, **kwargs):
            recorded.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"claims": [{
                "text": "거래 표본이 부족해 가격 우열 판단은 보류해야 합니다.",
                "evidence_ids": ["trades"]}]}, ensure_ascii=False))])

    monkeypatch.setattr(narrative.llm, "client", lambda feature, uid: recorded.append((feature, uid)) or
                        SimpleNamespace(messages=FakeMessages()))
    result = narrative._generate(7, report, "easy", "")
    assert recorded[0] == ("report_explanation", 7)
    assert recorded[1]["model"] == narrative.MODEL
    assert result["claims"][0]["evidence_ids"] == ["trades"]
    assert report["report_id"] == result["report_id"]


def test_route_rechecks_report_id_and_private_access(monkeypatch):
    report = _listing()
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    permission = {"allowed": True}
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: permission["allowed"])
    monkeypatch.setattr(reports_v2, "listing_report", lambda request, key: JSONResponse(report))
    data = {"type": "listing", "key": "일반매물:one", "mode": "easy"}
    response = reports_v2.explanation_request(None, report["report_id"], data)
    assert response.status_code == 202
    job_id = json.loads(response.body)["job_id"]
    with pytest.raises(HTTPException) as changed:
        reports_v2.explanation_request(None, "b" * 64, data)
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException) as bad_mode:
        reports_v2.explanation_request(None, report["report_id"], {**data, "mode": []})
    assert bad_mode.value.status_code == 422
    permission["allowed"] = False
    with pytest.raises(HTTPException) as private:
        reports_v2.explanation_request(None, report["report_id"], data)
    assert private.value.status_code == 403
    with pytest.raises(HTTPException) as hidden:
        reports_v2.explanation_job(None, job_id)
    assert hidden.value.status_code == 404


def test_question_validation_and_daily_limit(monkeypatch):
    report = _listing()
    with pytest.raises(ValueError):
        narrative.enqueue(7, report, "question", "", private_source=False)
    with pytest.raises(ValueError):
        narrative.enqueue(7, report, "easy", "extra", private_source=False)
    with pytest.raises(ValueError):
        narrative.enqueue(7, report, [], "", private_source=False)
    monkeypatch.setattr(narrative, "MAX_JOBS_PER_DAY", 2)
    narrative.enqueue(7, report, "question", "근거가 무엇인가요?", private_source=False)
    narrative.enqueue(7, report, "question", "가격은 확인되나요?", private_source=False)
    with pytest.raises(RuntimeError, match="daily_request_limit"):
        narrative.enqueue(7, report, "question", "다른 질문입니다", private_source=False)
