import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import playstat_proxy


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        if headers is not None:
            self.headers = headers
        elif json_data is not None:
            self.headers = {"content-type": "application/json"}
        else:
            self.headers = {"content-type": "text/plain"}
        self.content = (
            json.dumps(json_data).encode() if json_data is not None else text.encode()
        )

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


class FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that records the outbound call and
    returns (or raises) whatever the test configured on the class."""

    response = None
    error = None
    last_call = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, params=None, headers=None):
        FakeAsyncClient.last_call = {"url": url, "params": params, "headers": headers}
        if FakeAsyncClient.error is not None:
            raise FakeAsyncClient.error
        return FakeAsyncClient.response


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch):
    FakeAsyncClient.response = None
    FakeAsyncClient.error = None
    FakeAsyncClient.last_call = None
    monkeypatch.setattr(playstat_proxy.httpx, "AsyncClient", FakeAsyncClient)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_injects_api_key_header_when_configured(client, monkeypatch):
    monkeypatch.setattr(playstat_proxy.settings, "playstat_api_key", "secret123")
    FakeAsyncClient.response = FakeResponse(200, json_data={"ok": True})

    resp = client.get("/playstat/games")

    assert resp.status_code == 200
    assert FakeAsyncClient.last_call["headers"] == {"X-API-Key": "secret123"}


def test_no_api_key_header_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(playstat_proxy.settings, "playstat_api_key", "")
    FakeAsyncClient.response = FakeResponse(200, json_data={"ok": True})

    resp = client.get("/playstat/games")

    assert resp.status_code == 200
    assert FakeAsyncClient.last_call["headers"] == {}


def test_query_params_are_forwarded(client, monkeypatch):
    monkeypatch.setattr(playstat_proxy.settings, "playstat_api_key", "")
    FakeAsyncClient.response = FakeResponse(200, json_data=[])

    resp = client.get("/playstat/games", params={"date": "2026-07-15", "sport": "MLB"})

    assert resp.status_code == 200
    assert FakeAsyncClient.last_call["params"] == {"date": "2026-07-15", "sport": "MLB"}
    assert FakeAsyncClient.last_call["url"] == f"{playstat_proxy.settings.playstat_base_url}/games"


def test_upstream_401_is_passed_through(client, monkeypatch):
    monkeypatch.setattr(playstat_proxy.settings, "playstat_api_key", "")
    FakeAsyncClient.response = FakeResponse(401, json_data={"detail": "unauthorized"})

    resp = client.get("/playstat/games")

    assert resp.status_code == 401
    assert resp.json() == {"detail": "unauthorized"}


def test_upstream_200_json_body_returned_intact(client, monkeypatch):
    monkeypatch.setattr(playstat_proxy.settings, "playstat_api_key", "")
    payload = {"games": [{"id": 1, "home": "NYY", "away": "BOS"}]}
    FakeAsyncClient.response = FakeResponse(200, json_data=payload)

    resp = client.get("/playstat/games")

    assert resp.status_code == 200
    assert resp.json() == payload


def test_request_error_returns_502(client, monkeypatch):
    monkeypatch.setattr(playstat_proxy.settings, "playstat_api_key", "")
    FakeAsyncClient.error = httpx.ConnectError("connection refused")

    resp = client.get("/playstat/games")

    assert resp.status_code == 502
    assert resp.json() == {"detail": "playstat upstream unavailable"}
