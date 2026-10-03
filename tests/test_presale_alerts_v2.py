"""Presale D-0 events require an exact, freshly checked announcement date."""

from datetime import date, timedelta
import time

import pytest

from realty_signal import db
from realty_signal.services import presale_alerts_v2 as alerts


TODAY = date(2026, 10, 4)


def _watch():
    return {"key": "청약:123", "name": "새아파트", "region": "노원구"}


def _row(day="2026-10-04", *, stale=False):
    return {"key": "청약:123", "유형": "청약", "지표값": "접수중",
            "stale": stale, "ref": {"다음일정": day}, "source_checked_at": 1234}


def test_d0_is_once_only_and_owner_scoped():
    db.listing_watch_add(7, {"key": "청약:123", "유형": "청약", "단지명": "새아파트",
                             "지역": "노원구", "총액": None})
    assert alerts.materialize(7, [_watch()], [_row()], today=TODAY) == 1
    assert alerts.materialize(7, [_watch()], [_row()], today=TODAY) == 0
    from realty_signal.services import watch_alerts_v2
    feed = watch_alerts_v2.list_events(7, private_allowed=False)
    assert feed["unread"] == 1
    assert feed["items"][0]["kind"] == "presale_deadline"
    assert feed["items"][0]["payload"]["date"] == TODAY.isoformat()
    assert watch_alerts_v2.list_events(8, private_allowed=False)["items"] == []
    db.listing_watch_remove(7, "청약:123")
    assert watch_alerts_v2.list_events(7, private_allowed=False)["items"] == []


def test_future_missing_stale_invalid_and_muted_are_not_d0():
    db.listing_watch_add(7, {"key": "청약:123", "유형": "청약", "단지명": "새아파트",
                             "지역": "노원구", "총액": None})
    saved = [_watch()]
    assert alerts.materialize(7, saved, [_row("2026-10-05")], today=TODAY) == 0
    assert alerts.materialize(7, saved, [], today=TODAY) == 0
    assert alerts.materialize(7, saved, [_row(stale=True)], today=TODAY) == 0
    assert alerts.materialize(7, saved, [_row("not-a-date")], today=TODAY) == 0
    assert alerts.materialize(7, saved, [_row()], today=TODAY,
                              prefs={"presale_deadline": False}) == 0
    assert alerts.materialize(8, saved, [_row()], today=TODAY) == 0
    assert alerts.materialize(7, saved, [_row()], today=TODAY + timedelta(days=1)) == 0


def test_due_job_refreshes_once_and_uses_source_success(monkeypatch):
    from realty_signal import api, config

    db.listing_watch_add(7, {"key": "청약:123", "유형": "청약", "단지명": "새아파트",
                             "지역": "노원구", "총액": None})
    monkeypatch.setattr(config, "public_data_key", lambda: "synthetic-key")
    monkeypatch.setattr(alerts, "korea_today", lambda: TODAY)

    class Source:
        clears = 0
        ok = True

        def cache_clear(self):
            self.clears += 1

        def __call__(self):
            db.kv_set("presale_fetch_status", {"ok": self.ok, "ts": time.time()})
            return [{"관리번호": "123", "단지명": "새아파트", "지역": "노원구",
                     "상태": "접수중", "다음일정": TODAY.isoformat()}] if self.ok else []

    source = Source()
    monkeypatch.setattr(api, "_presale", source)
    assert alerts.scan_fresh_announcements() == 1
    assert alerts.scan_fresh_announcements() == 0
    assert source.clears == 2
    source.ok = False
    with pytest.raises(RuntimeError, match="presale_source_unavailable"):
        alerts.scan_fresh_announcements()


def test_no_saved_notice_does_not_touch_public_source(monkeypatch):
    from realty_signal import api, config

    monkeypatch.setattr(config, "public_data_key", lambda: "synthetic-key")
    monkeypatch.setattr(api, "_presale", lambda: pytest.fail("source called without saved notices"))
    assert alerts.scan_fresh_announcements() == 0


def test_saved_notice_dday_recomputes_from_absolute_date(monkeypatch):
    from realty_signal.services import listing_watch

    monkeypatch.setattr(alerts, "korea_today", lambda: TODAY)
    row = _row("2026-10-05")
    row.update({"단지명": "새아파트", "지역": "노원구", "총액": None})
    row["ref"]["Dday"] = 9  # Old cached relative day must not override the date.
    assert listing_watch.public_fields(row)["dday"] == 1


def test_presale_list_recomputes_cached_relative_day():
    from realty_signal import api

    tomorrow = (alerts.korea_today() + timedelta(days=1)).isoformat()
    cached = {"단지명": "새아파트", "청약접수시작": tomorrow,
              "청약접수마감": tomorrow, "Dday": 9, "상태": "공고"}
    current = api._presale_current(cached)
    assert current["Dday"] == 1
    assert current["상태"] == "접수예정"
    assert current["다음일정"] == tomorrow
