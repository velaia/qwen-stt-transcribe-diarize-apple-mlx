"""Caching of the built SPA: index.html must revalidate, hashed assets must not."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import WEB_DIST
from tests.test_server_helpers import build_test_app

pytestmark = pytest.mark.skipif(
    not (WEB_DIST / "index.html").exists(), reason="web/dist not built"
)


def test_index_html_is_revalidated_so_updates_reach_the_browser(tmp_path):
    with TestClient(build_test_app(tmp_path / "data")) as client:
        for path in ("/", "/some/client/route"):
            resp = client.get(path)
            assert resp.status_code == 200
            assert resp.headers["cache-control"] == "no-cache"


def test_fingerprinted_assets_are_cached_forever(tmp_path):
    asset = next((WEB_DIST / "assets").glob("*.js"))
    with TestClient(build_test_app(tmp_path / "data")) as client:
        resp = client.get(f"/assets/{asset.name}")
        assert resp.status_code == 200
        assert "immutable" in resp.headers["cache-control"]
