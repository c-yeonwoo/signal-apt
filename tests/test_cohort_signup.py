"""수강생 cohort 게이트 — invite / allowlist / prod 차단."""

from __future__ import annotations

from realty_signal import auth, config, db


def test_local_open_without_cohort_env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    monkeypatch.delenv("INVITE_CODES", raising=False)
    monkeypatch.delenv("STUDENT_ALLOWLIST", raising=False)
    monkeypatch.delenv("SIGNUP_OPEN", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    tok, err = auth.signup("dev@example.com", "secret1", accept_tos=True)
    assert err is None and tok


def test_prod_blocks_without_cohort_env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    monkeypatch.delenv("INVITE_CODES", raising=False)
    monkeypatch.delenv("STUDENT_ALLOWLIST", raising=False)
    monkeypatch.delenv("SIGNUP_OPEN", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    tok, err = auth.signup("x@example.com", "secret1", accept_tos=True)
    assert tok is None and "초대" in (err or "")


def test_invite_code_required_and_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    monkeypatch.setenv("INVITE_CODES", "Cohort2026A, other")
    monkeypatch.delenv("STUDENT_ALLOWLIST", raising=False)
    monkeypatch.delenv("SIGNUP_OPEN", raising=False)
    tok, err = auth.signup("a@example.com", "secret1", accept_tos=True)
    assert tok is None and "초대 코드" in (err or "")
    tok, err = auth.signup("a@example.com", "secret1", accept_tos=True, invite_code="wrong")
    assert tok is None and "올바르지" in (err or "")
    tok, err = auth.signup("a@example.com", "secret1", accept_tos=True, invite_code="cohort2026a")
    assert err is None and tok
    u = db.user_by_email("a@example.com")
    p = db.profile_get(u["id"])
    assert p.get("cohort") == "student"
    assert p.get("invite_code") == "cohort2026a"


def test_allowlist_bypasses_invite(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    monkeypatch.setenv("STUDENT_ALLOWLIST", "vip@example.com")
    monkeypatch.setenv("INVITE_CODES", "needcode")
    monkeypatch.delenv("SIGNUP_OPEN", raising=False)
    tok, err = auth.signup("vip@example.com", "secret1", accept_tos=True)
    assert err is None and tok


def test_check_cohort_access_unit(monkeypatch):
    monkeypatch.setenv("INVITE_CODES", "abc")
    monkeypatch.delenv("STUDENT_ALLOWLIST", raising=False)
    monkeypatch.delenv("SIGNUP_OPEN", raising=False)
    assert config.check_cohort_access("x@y.com", None)
    assert config.check_cohort_access("x@y.com", "abc") is None
