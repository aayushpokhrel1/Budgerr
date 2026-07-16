import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_auth_disabled_by_default_allows_request_with_no_key(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "API_KEYS", {})

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_auth_enabled_missing_key_returns_401(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEYS", {"web": "secret123"})

    resp = client.get("/health")

    assert resp.status_code == 401
    assert resp.json() == {"detail": "missing or invalid API key"}


def test_auth_enabled_wrong_key_returns_401(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEYS", {"web": "secret123"})

    resp = client.get("/health", headers={"X-API-Key": "wrong-key"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "missing or invalid API key"}


def test_auth_enabled_valid_key_returns_200(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "API_KEYS", {"web": "secret123", "mobile": "othersecret"})

    resp = client.get("/health", headers={"X-API-Key": "secret123"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
