"""Open tabs must be able to detect a UI deployment without exposing market data."""

from fastapi.testclient import TestClient

from realty_signal import api


def test_index_and_public_version_share_a_content_revision():
    client = TestClient(api.app)
    page = client.get("/")
    version_response = client.get("/api/client-version")

    assert page.status_code == version_response.status_code == 200
    version = version_response.json()["client_version"]
    assert version and version in page.text
    assert api._CLIENT_VERSION_MARKER not in page.text
    assert page.headers["etag"] == f'W/"{version}"'
    assert page.headers["cache-control"] == "no-cache, must-revalidate"
    assert version_response.headers["cache-control"] == "no-store"
    cached = client.get("/", headers={"If-None-Match": page.headers["etag"]})
    assert cached.status_code == 304 and not cached.content


def test_revision_changes_for_same_length_html_or_js_edits(tmp_path):
    html = tmp_path / "index.html"
    script = tmp_path / "signal-v2.js"
    html.write_text("<html>a</html>", encoding="utf-8")
    script.write_text("const x=1", encoding="utf-8")
    first = api._client_version_for(tmp_path)
    script.write_text("const x=2", encoding="utf-8")
    second = api._client_version_for(tmp_path)
    html.write_text("<html>b</html>", encoding="utf-8")
    third = api._client_version_for(tmp_path)
    assert len({first, second, third}) == 3
