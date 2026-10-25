"""Раздача собранного клиента: файлы сборки, возврат index.html для маршрутов клиента, заголовки."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from expsched.api.app import create_app
from expsched.api.store import RunStore

INDEX = '<!doctype html><div id="root"></div><script type="module" src="/assets/index-abc.js"></script>'


@pytest.fixture
def client(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("s3cr3t-content", encoding="utf-8")
    with TestClient(create_app(store=RunStore(tmp_path / "runs"), web_dir=dist)) as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/builder", "/runs/0123456789abcdef/explain", "/compare?run=x"])
def test_client_routes_get_index(client, path):
    response = client.get(path)
    assert response.status_code == 200 and response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_hashed_assets_are_cached_forever(client):
    response = client.get("/assets/index-abc.js")
    assert response.status_code == 200 and response.text == "console.log(1)"
    assert "immutable" in response.headers["cache-control"]
    assert client.get("/favicon.svg").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/assets/missing.js", "/v1/nope", "/v1/runs/0123456789abcdef"])
def test_missing_files_and_api_paths_are_json_404(client, path):
    response = client.get(path)
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("path", ["/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt"])
def test_files_outside_the_build_are_not_served(client, path):
    response = client.get(path)
    assert "s3cr3t-content" not in response.text


def test_api_and_docs_still_answer(client):
    assert "fifo" in client.get("/v1/policies").text
    assert client.get("/openapi.json").json()["info"]["title"] == "expsched"
    assert all(not path.startswith("/{") for path in client.get("/openapi.json").json()["paths"])


def test_every_response_has_nosniff(client):
    for path in ("/", "/v1/policies", "/v1/nope"):
        headers = client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff" and headers["referrer-policy"] == "no-referrer"


def test_without_a_build_only_the_api_answers(tmp_path):
    client = TestClient(create_app(store=RunStore(tmp_path)))
    assert client.get("/builder").status_code == 404
    assert client.get("/v1/policies").status_code == 200
