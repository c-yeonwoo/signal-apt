"""Security contracts exercise concurrent calls, not implementation strings."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from realty_signal import api, config
from realty_signal.brain import memory


def test_advisor_identity_is_bound_to_each_request(monkeypatch):
    monkeypatch.setattr(api, "_fav_context", lambda uid: {"owner": uid})
    monkeypatch.setattr(memory, "load", lambda uid: {"owner": uid})
    monkeypatch.setattr(memory, "to_public", lambda obj: obj)
    monkeypatch.setattr(api.db, "profile_get", lambda uid: {})
    barrier = Barrier(2)

    def run(uid):
        execute = api.advisor_tools(uid)
        barrier.wait(timeout=5)
        return execute("get_user_context", {"uid": 999})

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(run, (101, 202)))
    assert a["favorites"]["owner"] == a["memory"]["owner"] == 101
    assert b["favorites"]["owner"] == b["memory"]["owner"] == 202
    assert api._advisor_tool("get_user_context", {"uid": 101}) == {"error": "login_required"}


def test_model_entitlement_does_not_grant_admin(monkeypatch):
    monkeypatch.setenv("AI_OPUS_WHITELIST", "premium@example.com")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    assert config.opus_whitelist() == {"premium@example.com"}
    assert config.admin_whitelist() == set()
    monkeypatch.setenv("ADMIN_EMAILS", "operator@example.com")
    assert config.admin_whitelist() == {"operator@example.com"}
