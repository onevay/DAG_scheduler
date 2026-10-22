import json
from pathlib import Path

import httpx
import pytest
from expsched_ui.client import ApiClient, ApiUnavailable
from fastapi.testclient import TestClient

from expsched.api.app import app

DEMO = json.loads(
    (Path(__file__).resolve().parents[2] / "scenarios" / "demo.json").read_text(encoding="utf-8")
)
client = ApiClient(http=TestClient(app))


def test_policies_and_validation_through_the_api():
    assert "priority" in client.policies()["policies"]
    assert client.validate(DEMO, "lenient") == (200, {"valid": True, "diagnostics": []})


def test_run_through_the_api():
    status, body = client.run(DEMO, {"name": "fifo"})
    assert status == 200 and body["result"]["policy"] == "fifo"


def test_unreachable_api_is_reported():
    offline = ApiClient("http://127.0.0.1:9", timeout=0.5)  # порт 9 никто не слушает
    with pytest.raises(ApiUnavailable):
        offline.policies()


def test_transport_errors_are_wrapped():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiUnavailable):
        ApiClient(http=httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://x")).policies()


def test_non_json_answer_is_reported_instead_of_crashing():
    # так отвечает, например, прокси или чужой сервис на том же порту
    def html(request):
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    with pytest.raises(ApiUnavailable, match="502"):
        ApiClient(http=httpx.Client(transport=httpx.MockTransport(html), base_url="http://x")).policies()


def test_system_proxy_is_not_used(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    assert ApiClient("http://127.0.0.1:8000")._http.trust_env is False
