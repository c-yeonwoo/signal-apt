"""텔레그램 연결 흐름 — 딥링크 코드 발급 → /start 폴링 → chat_id 저장."""

from __future__ import annotations

import pytest

from realty_signal import auth, db, telegram


@pytest.fixture
def uid(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.delenv("INVITE_CODES", raising=False)
    monkeypatch.delenv("STUDENT_ALLOWLIST", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    token, err = auth.signup("brief@test.io", "secret1", accept_tos=True)
    assert err is None
    return db.session_user(token)["id"]


def _fake_calls(monkeypatch, updates: list[dict]):
    """getUpdates 응답을 주입하고 sendMessage 호출을 수집."""
    sent: list[tuple] = []

    def call(method, payload=None, timeout=15):
        if method == "getUpdates":
            return {"ok": True, "result": updates}
        if method == "getMe":
            return {"ok": True, "result": {"username": "signalapt_bot"}}
        if method == "sendMessage":
            sent.append((payload["chat_id"], payload["text"]))
            return {"ok": True, "result": {}}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(telegram, "_call", call)
    return sent


def _start(code: str, chat_id: int = 777, update_id: int = 1) -> dict:
    return {"update_id": update_id,
            "message": {"chat": {"id": chat_id}, "from": {"username": "nick"},
                        "text": f"/start {code}"}}


def test_link_code_binds_chat_id(uid, monkeypatch):
    _fake_calls(monkeypatch, [])
    link = telegram.issue_link_code(uid)
    assert link["url"].endswith(link["code"])
    sent = _fake_calls(monkeypatch, [_start(link["code"])])
    stats = telegram.poll_updates()
    assert stats["linked"] == 1
    assert telegram.chat_id_of(db.profile_get(uid)) == 777
    assert sent and "연결됐습니다" in sent[0][1]


def test_expired_code_does_not_link(uid, monkeypatch):
    _fake_calls(monkeypatch, [_start("deadbeef")])
    stats = telegram.poll_updates()
    assert stats["linked"] == 0
    assert telegram.chat_id_of(db.profile_get(uid)) is None


def test_code_is_single_use(uid, monkeypatch):
    _fake_calls(monkeypatch, [])
    code = telegram.issue_link_code(uid)["code"]
    _fake_calls(monkeypatch, [_start(code)])
    telegram.poll_updates()
    telegram.unlink(uid)
    _fake_calls(monkeypatch, [_start(code, update_id=2)])
    assert telegram.poll_updates()["linked"] == 0


def test_offset_advances_so_updates_are_not_reprocessed(uid, monkeypatch):
    _fake_calls(monkeypatch, [])
    code = telegram.issue_link_code(uid)["code"]
    _fake_calls(monkeypatch, [_start(code, update_id=41)])
    telegram.poll_updates()
    assert db.kv_get(telegram.OFFSET_KEY) == 42


def test_stop_unlinks(uid, monkeypatch):
    _fake_calls(monkeypatch, [])
    code = telegram.issue_link_code(uid)["code"]
    _fake_calls(monkeypatch, [_start(code)])
    telegram.poll_updates()
    _fake_calls(monkeypatch, [{"update_id": 9, "message": {"chat": {"id": 777}, "text": "/stop"}}])
    stats = telegram.poll_updates()
    assert stats["stopped"] == 1
    assert telegram.chat_id_of(db.profile_get(uid)) is None


def test_users_with_telegram_lists_linked_only(uid, monkeypatch):
    tok, _ = auth.signup("plain@test.io", "secret1", accept_tos=True)
    db.profile_set(db.session_user(tok)["id"], {"가용자본": 30000})
    _fake_calls(monkeypatch, [])
    code = telegram.issue_link_code(uid)["code"]
    _fake_calls(monkeypatch, [_start(code)])
    telegram.poll_updates()
    rows = db.users_with_telegram()
    assert [r["id"] for r in rows] == [uid]
    assert rows[0]["chat_id"] == 777


def test_send_message_truncates(uid, monkeypatch):
    sent = _fake_calls(monkeypatch, [])
    assert telegram.send_message(777, "가" * 5000)
    assert len(sent[0][1]) <= telegram.MAX_LEN + 10


def test_unavailable_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert not telegram.available()
    assert telegram.poll_updates() == {"linked": 0, "stopped": 0, "seen": 0}
