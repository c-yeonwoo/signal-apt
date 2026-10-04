"""Retired Nick chat must not incur cost, while memory deletion stays available."""

from fastapi.testclient import TestClient

from realty_signal import advisor, api, auth, db


def test_old_chat_urls_return_gone_without_model_or_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "retired.db")
    db._migrated[0] = False
    token, error = auth.signup("retired-chat@example.com", "secret1", accept_tos=True)
    assert error is None

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Retired chat must not reserve usage or call a model")

    monkeypatch.setattr(advisor, "run_advisor", forbidden)
    monkeypatch.setattr(advisor, "run_advisor_stream", forbidden)
    monkeypatch.setattr(db, "usage_reserve", forbidden)
    guest = TestClient(api.app)
    assert guest.get("/api/advisor/memory").status_code == 401
    assert guest.delete("/api/advisor/memory").status_code == 401

    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, token)
    for path in ("/api/advisor", "/api/advisor/stream"):
        response = client.post(path, json={"messages": [{"role": "user", "text": "서울 시세?"}]})
        assert response.status_code == 410
        assert response.json()["reason"] == "retired"
    usage = client.get("/api/usage")
    assert usage.status_code == 200
    assert set(usage.json()) == {"ok", "report"}
    assert client.get("/api/advisor/memory").status_code == 200
