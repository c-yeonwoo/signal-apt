"""Only the active Telegram channel appears in notification readiness."""

from __future__ import annotations

from pathlib import Path

from realty_signal import db
from realty_signal.services import notify_status as ns


def test_no_token_blocks_telegram_without_email(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    status = ns.status(None)
    assert not status["all_ready"]
    assert [channel["channel"] for channel in status["channels"]] == ["telegram"]
    assert "봇 토큰" in status["blocked"][0]["reason"]
    assert status["blocked"][0]["how"]
    assert "시장이 조용한 것과 다릅니다" in status["note"]


def test_token_without_link_explains_connection(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x:y")
    channel = ns.status(None)["channels"][0]
    assert channel["available"] and not channel["linked"]
    assert "연결" in channel["reason"]


def test_linked_profile_is_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x:y")
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    db.profile_set(7, {"telegram": {"chat_id": 777, "linked_at": 10}})
    status = ns.status(7)
    assert status["all_ready"] and status["blocked"] == []
    assert status["channels"][0]["linked"]
    assert status["channels"][0]["last_ts"] is None
    db.kv_set("telegram_last_sent:7", {"kind": "briefing"})
    assert ns.status(7)["channels"][0]["last_ts"] is not None


def test_html_has_only_telegram_channel_guidance():
    src = Path("src/realty_signal/web/index.html").read_text(encoding="utf-8")
    assert "_loadNotifyStatus" in src and 'id="dashNotify"' in src
    assert "/api/notify-status" in src
    assert "텔레그램 브리핑·관심 소식" in src
    assert "이메일 주간 다이제스트" not in src
