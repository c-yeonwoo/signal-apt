"""알림 채널 상태 — '왜 안 나가는지'가 화면까지 도달하는지.

2026-09-06: 이메일·텔레그램이 7/26 이후 멈춰 있었는데 코드는 멀쩡했다.
설정이 없어서 안 나갔고, **그 사실이 어디에도 없었다.** 이 테스트는 그 침묵이
다시 돌아오지 않게 막는다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from realty_signal import db
from realty_signal.services import notify_status as ns

_HTML = Path(__file__).resolve().parents[1] / "src/realty_signal/web/index.html"


@pytest.fixture
def _clean_env(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_FROM", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def test_no_config_blocks_both_with_reason(_clean_env):
    st = ns.status(None)
    assert st["all_ready"] is False
    assert len(st["blocked"]) == 2
    for c in st["blocked"]:
        # 못 나가면 사유와 조치가 **둘 다** 있어야 한다
        assert c["reason"], c["channel"]
        assert c["how"], c["channel"]


def test_note_separates_silence_from_calm_market(_clean_env):
    note = ns.status(None)["note"]
    assert "시장이 조용한 것과 다릅니다" in note


def test_configured_email_unblocks(monkeypatch, _clean_env):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "a@example.com")
    chans = {c["channel"]: c for c in ns.status(None)["channels"]}
    # 관심지역이 없으면 여전히 막히지만 사유가 SMTP 가 아니라 관심지역이어야 한다
    assert "SMTP" not in (chans["email"].get("reason") or "")


def test_telegram_token_without_link_says_link_not_token(monkeypatch, _clean_env):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x:y")
    tg = {c["channel"]: c for c in ns.status(None)["channels"]}["telegram"]
    assert tg["available"] is True
    assert tg["linked"] is False
    assert "연결" in tg["reason"]


def test_record_digest_run_marks_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    # 연결 시 스키마가 자동 생성된다(_migrate)
    ns.record_digest_run({"total": 5, "sent": 0, "dry_run": 5, "errors": 0}, sent=False)
    got = db.kv_get(ns.DIGEST_STATS_KEY)
    assert got["was_dry_run"] is True
    assert got["dry_run"] == 5


def test_html_no_longer_says_preparing():
    """'준비 중입니다' 는 거짓이었다 — 준비가 아니라 토큰이 없는 것."""
    body = "\n".join(
        ln for ln in _HTML.read_text(encoding="utf-8").splitlines()
        if not re.match(r"\s*(\*|//|/\*)", ln)
    )
    assert "브리핑은 준비 중" not in body


def test_html_renders_notify_card():
    src = _HTML.read_text(encoding="utf-8")
    assert "_loadNotifyStatus" in src
    assert 'id="dashNotify"' in src
    assert "/api/notify-status" in src
